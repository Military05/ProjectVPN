from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow
from shop_bot.domain.vpn.builder import VlessBuildInput, VlessUriBuilder


PROFILE_TITLE = "SoterVPN"
PRIMARY_PROFILE_NAME = "🇩🇪 Основной"
DEFAULT_TOTAL_BYTES = 500 * 1024 * 1024 * 1024


@dataclass(slots=True)
class SubscriptionFeed:
    body: str
    profile_title: str
    upload_bytes: int
    download_bytes: int
    total_bytes: int
    expires_at: datetime | None


@dataclass(slots=True)
class XuiClientTraffic:
    upload_bytes: int
    download_bytes: int
    total_bytes: int | None


async def get_subscription_feed(
    container: ServiceContainer,
    *,
    client_uuid: str,
) -> SubscriptionFeed | None:
    now = utcnow()

    try:
        parsed_client_uuid = UUID(client_uuid)
    except ValueError:
        return None

    async with container.uow() as uow:
        vpn = await uow.vpn.get_active_configuration_by_client_uuid(
            parsed_client_uuid,
            now,
        )
        if vpn is None:
            return None

        latest_paid_period = await uow.subscriptions.get_latest_paid_period(
            int(vpn["subscription_id"]),
        )

    expires_at = None
    if latest_paid_period is not None:
        expires_at = latest_paid_period["expires_at"]

    vless_uri = VlessUriBuilder().build(
        VlessBuildInput(
            client_uuid=str(vpn["client_uuid"]),
            host=str(vpn["host"]),
            port=int(vpn["port"]),
            display_name=PRIMARY_PROFILE_NAME,
            security=vpn.get("security"),
            sni=vpn.get("sni"),
            fingerprint=vpn.get("fingerprint"),
            public_key=vpn.get("public_key"),
            short_id=vpn.get("short_id"),
            transport_type=vpn.get("transport_type"),
            flow=vpn.get("flow"),
            encryption=vpn.get("encryption"),
        )
    ).uri

    plain_body = f"{vless_uri}\n"
    encoded_body = base64.b64encode(plain_body.encode("utf-8")).decode("ascii")

    traffic = await _load_xui_client_traffic(
        settings=container.settings,
        vpn=vpn,
    )

    upload_bytes = 0
    download_bytes = 0
    total_bytes = _extract_total_bytes(vpn)

    if traffic is not None:
        upload_bytes = traffic.upload_bytes
        download_bytes = traffic.download_bytes

        if traffic.total_bytes is not None and traffic.total_bytes > 0:
            total_bytes = traffic.total_bytes

    return SubscriptionFeed(
        body=encoded_body,
        profile_title=PROFILE_TITLE,
        upload_bytes=upload_bytes,
        download_bytes=download_bytes,
        total_bytes=total_bytes,
        expires_at=expires_at,
    )


def subscription_userinfo_header(feed: SubscriptionFeed) -> str:
    expire = 0
    if feed.expires_at is not None:
        expire = int(feed.expires_at.timestamp())

    return (
        f"upload={feed.upload_bytes}; "
        f"download={feed.download_bytes}; "
        f"total={feed.total_bytes}; "
        f"expire={expire}"
    )


async def _load_xui_client_traffic(
    *,
    settings: Any,
    vpn: Mapping[str, Any],
) -> XuiClientTraffic | None:
    if not getattr(settings, "xui_base_url", None):
        return None

    if not getattr(settings, "xui_username", None):
        return None

    if not getattr(settings, "xui_password", None):
        return None

    inbound_id = _resolve_xui_inbound_id(settings=settings, vpn=vpn)
    if inbound_id is None:
        return None

    client_uuid = str(vpn["client_uuid"])
    display_name = str(vpn.get("display_name") or "")

    timeout_seconds = float(getattr(settings, "request_timeout_seconds", 15))

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            await _xui_login(client=client, settings=settings)

            response = await client.get(
                f"{str(settings.xui_base_url).rstrip('/')}/panel/api/inbounds/list"
            )
            response.raise_for_status()

            result = _xui_json_response(response)

            if result.get("success") is not True:
                return None

            return _find_client_traffic(
                inbounds=result.get("obj") or [],
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                display_name=display_name,
            )
    except Exception:
        return None


async def _xui_login(
    *,
    client: httpx.AsyncClient,
    settings: Any,
) -> None:
    login_response = await client.post(
        f"{str(settings.xui_base_url).rstrip('/')}/login",
        data={
            "username": settings.xui_username,
            "password": settings.xui_password,
        },
    )
    login_response.raise_for_status()

    result = _xui_json_response(login_response)
    if result.get("success") is not True:
        raise RuntimeError(f"3x-ui login failed: {result.get('msg') or result}")


def _find_client_traffic(
    *,
    inbounds: list[Any],
    inbound_id: int,
    client_uuid: str,
    display_name: str,
) -> XuiClientTraffic | None:
    for inbound in inbounds:
        if not isinstance(inbound, Mapping):
            continue

        if int(inbound.get("id") or 0) != inbound_id:
            continue

        stored_clients_by_uuid, stored_clients_by_email = _extract_stored_clients(inbound)

        for client_stat in inbound.get("clientStats") or []:
            if not isinstance(client_stat, Mapping):
                continue

            stat_uuid = str(client_stat.get("uuid") or "")
            stat_email = str(client_stat.get("email") or "")

            if stat_uuid != client_uuid and stat_email != display_name:
                continue

            stored_client = (
                stored_clients_by_uuid.get(client_uuid)
                or stored_clients_by_email.get(display_name)
                or {}
            )

            upload_bytes = _safe_int(client_stat.get("up"))
            download_bytes = _safe_int(client_stat.get("down"))

            total_bytes = (
                _safe_int_or_none(client_stat.get("total"))
                or _safe_int_or_none(client_stat.get("totalGB"))
                or _safe_int_or_none(stored_client.get("totalGB"))
                or _safe_int_or_none(stored_client.get("total"))
            )

            return XuiClientTraffic(
                upload_bytes=upload_bytes,
                download_bytes=download_bytes,
                total_bytes=total_bytes,
            )

        stored_client = (
            stored_clients_by_uuid.get(client_uuid)
            or stored_clients_by_email.get(display_name)
        )
        if stored_client is not None:
            return XuiClientTraffic(
                upload_bytes=0,
                download_bytes=0,
                total_bytes=(
                    _safe_int_or_none(stored_client.get("totalGB"))
                    or _safe_int_or_none(stored_client.get("total"))
                ),
            )

    return None


def _extract_stored_clients(
    inbound: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    settings_raw = inbound.get("settings")

    if isinstance(settings_raw, str) and settings_raw:
        try:
            inbound_settings = json.loads(settings_raw)
        except json.JSONDecodeError:
            inbound_settings = {}
    elif isinstance(settings_raw, Mapping):
        inbound_settings = settings_raw
    else:
        inbound_settings = {}

    by_uuid: dict[str, Mapping[str, Any]] = {}
    by_email: dict[str, Mapping[str, Any]] = {}

    for stored_client in inbound_settings.get("clients") or []:
        if not isinstance(stored_client, Mapping):
            continue

        stored_uuid = str(stored_client.get("id") or "")
        stored_email = str(stored_client.get("email") or "")

        if stored_uuid:
            by_uuid[stored_uuid] = stored_client

        if stored_email:
            by_email[stored_email] = stored_client

    return by_uuid, by_email


def _resolve_xui_inbound_id(
    *,
    settings: Any,
    vpn: Mapping[str, Any],
) -> int | None:
    candidates = [
        vpn.get("local_inbound_id"),
        vpn.get("inbound_id"),
        getattr(settings, "xui_inbound_id", None),
    ]

    for candidate in candidates:
        if candidate is None:
            continue

        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue

    return None


def _xui_json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"3x-ui returned non-JSON response: {response.text[:500]}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"3x-ui returned unexpected JSON response: {data!r}")

    return data


def _extract_total_bytes(vpn: Mapping[str, Any]) -> int:
    for key in ("total_bytes", "traffic_limit_bytes", "totalGB", "total_gb_bytes"):
        value = _safe_int_or_none(vpn.get(key))
        if value is not None and value > 0:
            return value

    traffic_limit_gb = _safe_int_or_none(vpn.get("traffic_limit_gb"))
    if traffic_limit_gb is not None and traffic_limit_gb > 0:
        return traffic_limit_gb * 1024 * 1024 * 1024

    return DEFAULT_TOTAL_BYTES


def _safe_int(value: Any) -> int:
    parsed = _safe_int_or_none(value)
    return parsed if parsed is not None else 0


def _safe_int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None