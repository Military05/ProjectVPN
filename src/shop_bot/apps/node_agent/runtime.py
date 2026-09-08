from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


from shop_bot.core.config import Settings
from shop_bot.core.time import utcnow


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

    async def snapshot(self) -> dict[str, Any]:
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

    async def snapshot(self) -> dict[str, Any]:
        health = await self.health()
        capabilities = await self.capabilities()
        status = await self.status()
        return {"health": health, "capabilities": capabilities, "status": status}


@dataclass(slots=True)
class XuiNodeRuntime:
    settings: Settings

    async def health(self) -> dict[str, Any]:
        return _health_payload(self.settings)

    async def capabilities(self) -> dict[str, Any]:
        return _capabilities_payload(self.settings, xui=True)

    async def provision_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        from shop_bot.infrastructure.xui import XuiClient, inbound_numeric_id
        inbound_id = _validate_xui_inbound(payload.get("inbound_id"))
        client = XuiClient(self.settings)
        await client.add_client(
            inbound_id=inbound_id,
            client_uuid=str(payload["client_uuid"]),
            email=str(payload["display_name"]),
            flow=payload.get("flow"),
            expires_at=payload.get("expires_at"),
        )
        return {"status": "provisioned", "client_uuid": str(payload["client_uuid"]), "remote_client_ref": str(payload["display_name"]), "applied_at": utcnow()}

    async def revoke_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        from shop_bot.infrastructure.xui import XuiClient, inbound_numeric_id
        inbound_id = _validate_xui_inbound(payload.get("inbound_id"))
        client = XuiClient(self.settings)
        deleted = await client.delete_client(
            inbound_id=inbound_id,
            client_uuid=str(payload["client_uuid"]),
        )
        if not deleted:
            raise RuntimeError("XUI client deletion could not be verified")
        return {"status": "revoked", "client_uuid": str(payload["client_uuid"]), "applied_at": utcnow()}

    async def status(self) -> dict[str, Any]:
        from shop_bot.infrastructure.xui import XuiClient, inbound_metrics, inbound_numeric_id
        client = XuiClient(self.settings)
        inbound = await client.get_inbound(inbound_id=self.settings.node_agent_xui_inbound_id or 1)
        active_clients, rx_bytes, tx_bytes = inbound_metrics(inbound)
        return {
            "node_id": self.settings.node_agent_node_key,
            "status": "online",
            "active_clients": active_clients,
            "max_clients": self.settings.node_agent_status_max_clients,
            "load": {"cpu_percent": None, "memory_percent": None, "disk_percent": None},
            "traffic": {"rx_bytes": rx_bytes, "tx_bytes": tx_bytes},
            "inbounds": [_inbound_from_settings(self.settings)],
        }

    async def snapshot(self) -> dict[str, Any]:
        health = await self.health()
        capabilities = await self.capabilities()
        status = await self.status()
        return {"health": health, "capabilities": capabilities, "status": status}


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


def _validate_xui_inbound(value: Any) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("inbound_id must be a positive integer for XUI runtime") from exc
    if parsed <= 0:
        raise ValueError("inbound_id must be a positive integer for XUI runtime")
    return parsed


def _health_payload(settings: Settings) -> dict[str, Any]:
    return {
        "status": "ok",
        "node_id": settings.node_agent_node_key,
        "agent_version": settings.node_agent_version,
        "time": utcnow(),
    }


def _capabilities_payload(settings: Settings, *, xui: bool) -> dict[str, Any]:
    return {
        "node_id": settings.node_agent_node_key,
        "supports": {"provision_client": True, "revoke_client": True, "metrics": True, "xui": xui, "direct_xray": False},
        "protocols": [settings.node_agent_protocol],
        "transports": [settings.node_agent_transport_type or "tcp"],
        "security": [settings.node_agent_security or "none"],
    }
