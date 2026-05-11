from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow
from shop_bot.domain.vpn.builder import VlessBuildInput, VlessUriBuilder


DEFAULT_SUBSCRIPTION_BASE_URL = "http://localhost:8080"


async def list_bot_tariffs(container: ServiceContainer) -> list[Mapping[str, Any]]:
    async with container.uow() as uow:
        return await uow.admin.list_tariffs(include_disabled=False)


async def get_user_dashboard(container: ServiceContainer, *, telegram_id: int) -> Mapping[str, Any]:
    now = utcnow()

    async with container.uow() as uow:
        user = await uow.users.get_user_by_contact("telegram_id", str(telegram_id))
        if user is None:
            return {"user": None, "subscription": None, "vpn_configuration": None}

        subscription = await uow.subscriptions.get_current_access_for_user(
            int(user["user_id"]),
            now,
        )
        vpn = await uow.vpn.get_active_configuration_for_user(
            int(user["user_id"]),
            now,
        )

        latest_paid_period = None
        if subscription is not None:
            latest_paid_period = await _get_latest_paid_period_for_subscription(
                uow.subscriptions,
                int(subscription["subscription_id"]),
            )

    vpn_uri = None
    subscription_url = None

    if vpn is not None:
        vpn_uri = VlessUriBuilder().build(
            VlessBuildInput(
                client_uuid=str(vpn["client_uuid"]),
                host=str(vpn["host"]),
                port=int(vpn["port"]),
                display_name=str(vpn["display_name"]),
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

        subscription_url = _build_subscription_url(str(vpn["client_uuid"]))

    subscription_payload = None
    if subscription is not None:
        subscription_payload = {
            "subscription_id": int(subscription["subscription_id"]),
            "tariff_id": int(subscription["tariff_id"]),
            "tariff_name": str(subscription["tariff_name"]),
            "status": str(subscription["status"]),
            "starts_at": subscription["starts_at"],
            "expires_at": (
                latest_paid_period["expires_at"]
                if latest_paid_period is not None
                else subscription["expires_at"]
            ),
        }

    return {
        "user": {
            "user_id": int(user["user_id"]),
            "name_or_nick": str(user["name_or_nick"]),
        },
        "subscription": subscription_payload,
        "vpn_configuration": (
            {
                "vpn_configuration_id": int(vpn["vpn_configuration_id"]),
                "status": str(vpn["status"]),
                "display_name": str(vpn["display_name"]),
                "client_uuid": str(vpn["client_uuid"]),
                "server_name": str(vpn["server_name"]),
                "host": str(vpn["host"]),
                "port": int(vpn["port"]),
                "uri": vpn_uri,
                "subscription_url": subscription_url,
            }
            if vpn
            else None
        ),
    }


async def _get_latest_paid_period_for_subscription(
    subscriptions_repository: Any,
    subscription_id: int,
) -> Mapping[str, Any] | None:
    method = getattr(subscriptions_repository, "get_latest_paid_period", None)
    if method is None:
        return None

    period = await method(subscription_id)
    if period is None:
        return None

    if bool(period.get("is_paid", True)):
        return period

    return None


def _build_subscription_url(client_uuid: str) -> str:
    base_url = os.getenv("PUBLIC_SUBSCRIPTION_BASE_URL", DEFAULT_SUBSCRIPTION_BASE_URL)
    base_url = base_url.rstrip("/")
    return f"{base_url}/sub/{client_uuid}"