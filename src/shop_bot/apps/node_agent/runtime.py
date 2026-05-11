from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

from shop_bot.core.config import Settings
from shop_bot.core.time import utcnow


XUI_DEFAULT_TOTAL_GB_BYTES = 500 * 1024 * 1024 * 1024


class NodeRuntime(Protocol):
    async def provision_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def revoke_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def health(self) -> dict[str, Any]:
        ...

    async def capabilities(self) -> dict[str, Any]:
        ...

    async def status(self) -> dict[str, Any]:
        ...


@dataclass(slots=True)
class StubNodeRuntime:
    settings: Settings
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def provision_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_uuid = str(payload["client_uuid"])
        if client_uuid in self.clients:
            return {
                "status": "already_exists",
                "client_uuid": client_uuid,
                "remote_client_ref": self.clients[client_uuid]["remote_client_ref"],
                "applied_at": utcnow(),
            }

        remote_client_ref = f"stub:{payload['inbound_id']}:{payload['display_name']}"
        self.clients[client_uuid] = {
            "display_name": str(payload["display_name"]),
            "inbound_id": str(payload["inbound_id"]),
            "remote_client_ref": remote_client_ref,
            "expires_at": payload.get("expires_at"),
            "metadata": dict(payload.get("metadata") or {}),
        }

        return {
            "status": "provisioned",
            "client_uuid": client_uuid,
            "remote_client_ref": remote_client_ref,
            "applied_at": utcnow(),
        }

    async def revoke_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_uuid = str(payload["client_uuid"])
        removed = self.clients.pop(client_uuid, None)

        if removed is None:
            return {
                "status": "not_found_treated_as_success",
                "client_uuid": client_uuid,
                "applied_at": utcnow(),
            }

        return {
            "status": "revoked",
            "client_uuid": client_uuid,
            "remote_client_ref": removed["remote_client_ref"],
            "applied_at": utcnow(),
        }

    async def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "node_id": self.settings.node_agent_node_key,
            "agent_version": self.settings.node_agent_version,
            "time": utcnow(),
        }

    async def capabilities(self) -> dict[str, Any]:
        return {
            "node_id": self.settings.node_agent_node_key,
            "supports": {
                "provision_client": True,
                "revoke_client": True,
                "metrics": True,
                "xui": self.settings.node_agent_runtime_mode == "xui",
                "direct_xray": False,
            },
            "protocols": [self.settings.node_agent_protocol],
            "transports": [self.settings.node_agent_transport_type or "tcp"],
            "security": [self.settings.node_agent_security or "none"],
        }

    async def status(self) -> dict[str, Any]:
        return {
            "node_id": self.settings.node_agent_node_key,
            "status": "online",
            "active_clients": len(self.clients),
            "max_clients": self.settings.node_agent_status_max_clients,
            "load": {"cpu_percent": 0.0, "memory_percent": 0.0, "disk_percent": 0.0},
            "traffic": {"rx_bytes": 0, "tx_bytes": 0},
            "inbounds": [_inbound_from_settings(self.settings)],
        }


@dataclass(slots=True)
class XuiNodeRuntime(StubNodeRuntime):
    async def provision_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.xui_base_url or not self.settings.xui_username or not self.settings.xui_password:
            return {
                "status": "failed",
                "client_uuid": str(payload["client_uuid"]),
                "error_code": "XUI_CONFIG_MISSING",
                "message": "XUI credentials are not configured",
            }

        client_uuid = str(payload["client_uuid"])
        display_name = str(payload["display_name"])
        inbound_id = _inbound_numeric_id(payload["inbound_id"])
        expires_at_ms = _expires_at_to_xui_ms(payload.get("expires_at"))
        sub_id = _make_sub_id()

        xui_client = {
            "id": client_uuid,
            "email": display_name,
            "flow": str(payload.get("flow") or ""),
            "limitIp": 0,
            "totalGB": XUI_DEFAULT_TOTAL_GB_BYTES,
            "expiryTime": expires_at_ms,
            "enable": True,
            "tgId": "",
            "subId": sub_id,
            "reset": 0,
            "security": "auto",
        }

        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            await _xui_login(client, self.settings)

            response = await client.post(
                f"{self.settings.xui_base_url.rstrip('/')}/panel/api/inbounds/addClient",
                json={
                    "id": inbound_id,
                    "settings": json.dumps(
                        {"clients": [xui_client]},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            )
            response.raise_for_status()
            result = _xui_json_response(response)

            if result.get("success") is not True:
                return {
                    "status": "failed",
                    "client_uuid": client_uuid,
                    "remote_client_ref": display_name,
                    "error_code": "XUI_ADD_CLIENT_FAILED",
                    "message": str(result.get("msg") or result),
                    "applied_at": utcnow(),
                }

            verified = await _xui_client_exists(
                client=client,
                settings=self.settings,
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                display_name=display_name,
            )

            if not verified:
                return {
                    "status": "failed",
                    "client_uuid": client_uuid,
                    "remote_client_ref": display_name,
                    "error_code": "XUI_CLIENT_NOT_FOUND_AFTER_ADD",
                    "message": "3x-ui returned success, but the created client was not found in inbound list",
                    "applied_at": utcnow(),
                }

        return {
            "status": "provisioned",
            "client_uuid": client_uuid,
            "remote_client_ref": display_name,
            "applied_at": utcnow(),
        }

    async def revoke_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.xui_base_url or not self.settings.xui_username or not self.settings.xui_password:
            return {
                "status": "failed",
                "client_uuid": str(payload["client_uuid"]),
                "error_code": "XUI_CONFIG_MISSING",
                "message": "XUI credentials are not configured",
            }

        client_uuid = str(payload["client_uuid"])
        inbound_id = _inbound_numeric_id(payload["inbound_id"])

        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            await _xui_login(client, self.settings)

            response = await client.post(
                f"{self.settings.xui_base_url.rstrip('/')}/panel/api/inbounds/delClient/{client_uuid}",
                json={"id": inbound_id},
            )
            response.raise_for_status()
            result = _xui_json_response(response)

            if result.get("success") is not True:
                return {
                    "status": "failed",
                    "client_uuid": client_uuid,
                    "error_code": "XUI_DELETE_CLIENT_FAILED",
                    "message": str(result.get("msg") or result),
                    "applied_at": utcnow(),
                }

            still_exists = await _xui_client_exists(
                client=client,
                settings=self.settings,
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                display_name=None,
            )

            if still_exists:
                return {
                    "status": "failed",
                    "client_uuid": client_uuid,
                    "error_code": "XUI_CLIENT_STILL_EXISTS_AFTER_DELETE",
                    "message": "3x-ui returned success, but the client is still present in inbound list",
                    "applied_at": utcnow(),
                }

        return {
            "status": "revoked",
            "client_uuid": client_uuid,
            "applied_at": utcnow(),
        }


def build_runtime(settings: Settings) -> NodeRuntime:
    if settings.node_agent_runtime_mode == "xui":
        return XuiNodeRuntime(settings=settings)
    return StubNodeRuntime(settings=settings)


def _inbound_from_settings(settings: Settings) -> dict[str, Any]:
    return {
        "local_inbound_id": settings.node_agent_inbound_id,
        "port": settings.node_agent_public_port,
        "protocol": settings.node_agent_protocol,
        "security": settings.node_agent_security,
        "transport_type": settings.node_agent_transport_type,
        "public_host": settings.node_agent_public_host,
        "sni": settings.node_agent_sni,
        "fingerprint": settings.node_agent_fingerprint,
        "public_key": settings.node_agent_public_key,
        "short_id": settings.node_agent_short_id,
        "flow": settings.node_agent_flow,
        "encryption": settings.node_agent_encryption,
    }


async def _xui_login(client: httpx.AsyncClient, settings: Settings) -> None:
    login_response = await client.post(
        f"{settings.xui_base_url.rstrip('/')}/login",
        data={
            "username": settings.xui_username,
            "password": settings.xui_password,
        },
    )
    login_response.raise_for_status()

    result = _xui_json_response(login_response)
    if result.get("success") is not True:
        raise RuntimeError(f"3x-ui login failed: {result.get('msg') or result}")


async def _xui_client_exists(
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    inbound_id: int,
    client_uuid: str,
    display_name: str | None,
) -> bool:
    response = await client.get(f"{settings.xui_base_url.rstrip('/')}/panel/api/inbounds/list")
    response.raise_for_status()
    result = _xui_json_response(response)

    if result.get("success") is not True:
        raise RuntimeError(f"3x-ui inbound list failed: {result.get('msg') or result}")

    inbounds = result.get("obj") or []
    for inbound in inbounds:
        if int(inbound.get("id") or 0) != inbound_id:
            continue

        for client_stat in inbound.get("clientStats") or []:
            if str(client_stat.get("uuid") or "") == client_uuid:
                return True
            if display_name and str(client_stat.get("email") or "") == display_name:
                return True

        settings_raw = inbound.get("settings")
        if isinstance(settings_raw, str) and settings_raw:
            try:
                inbound_settings = json.loads(settings_raw)
            except json.JSONDecodeError:
                inbound_settings = {}
        elif isinstance(settings_raw, dict):
            inbound_settings = settings_raw
        else:
            inbound_settings = {}

        for stored_client in inbound_settings.get("clients") or []:
            if str(stored_client.get("id") or "") == client_uuid:
                return True
            if display_name and str(stored_client.get("email") or "") == display_name:
                return True

    return False


def _xui_json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"3x-ui returned non-JSON response: {response.text[:500]}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"3x-ui returned unexpected JSON response: {data!r}")

    return data


def _expires_at_to_xui_ms(raw: Any) -> int:
    if raw is None:
        return 0

    if isinstance(raw, datetime):
        return int(raw.timestamp() * 1000)

    value = str(raw).strip()
    if not value:
        return 0

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid expires_at value for XUI runtime: {raw!r}") from exc

    return int(parsed.timestamp() * 1000)


def _make_sub_id() -> str:
    return secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]


def _inbound_numeric_id(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Inbound id must be numeric for XUI runtime: {raw!r}") from exc