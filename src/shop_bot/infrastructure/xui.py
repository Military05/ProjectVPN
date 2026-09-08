from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from shop_bot.core.config import Settings


class XuiClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.xui_base_url or not settings.xui_username or not settings.xui_password:
            raise RuntimeError("XUI credentials are not configured")
        self.base_url = settings.xui_base_url.rstrip("/")
        self.username = settings.xui_username
        self.password = settings.xui_password
        self.timeout = settings.request_timeout_seconds

    async def _login(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            f"{self.base_url}/login",
            data={"username": self.username, "password": self.password},
        )
        response.raise_for_status()
        self._validated_body(response)

    @staticmethod
    def _validated_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("XUI returned a non-JSON application response") from exc
        if not isinstance(body, dict):
            raise RuntimeError("XUI returned a non-object application response")
        if body.get("success") is not True:
            message = str(body.get("msg") or body.get("message") or "XUI logical request failed")
            raise RuntimeError(f"XUI logical request failed: {message}")
        return body

    async def add_client(
        self,
        *,
        inbound_id: int,
        client_uuid: str,
        email: str,
        flow: str | None,
        expires_at: datetime | str | None,
    ) -> None:
        expiry_ms = _expiry_milliseconds(expires_at)
        client_payload: dict[str, Any] = {
            "id": client_uuid,
            "email": email,
            "flow": flow or "",
            "expiryTime": expiry_ms,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            await self._login(client)
            response = await client.post(
                f"{self.base_url}/panel/api/inbounds/addClient",
                json={"id": inbound_id, "settings": {"clients": [client_payload]}},
            )
            response.raise_for_status()
            self._validated_body(response)

    async def delete_client(self, *, inbound_id: int, client_uuid: str) -> bool:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            await self._login(client)
            response = await client.post(
                f"{self.base_url}/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}"
            )
            ambiguous = response.status_code == 404
            if not ambiguous:
                response.raise_for_status()
                try:
                    self._validated_body(response)
                    return True
                except RuntimeError as exc:
                    text = str(exc).lower()
                    ambiguous = any(token in text for token in ("not found", "not exist", "does not exist"))
                    if not ambiguous:
                        raise
            inbound = await self._get_inbound_with_client(client, inbound_id)
            return not _inbound_contains_uuid(inbound, client_uuid)

    async def get_inbound(self, *, inbound_id: int) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            await self._login(client)
            return await self._get_inbound_with_client(client, inbound_id)

    async def _get_inbound_with_client(
        self, client: httpx.AsyncClient, inbound_id: int
    ) -> dict[str, Any]:
        response = await client.get(f"{self.base_url}/panel/api/inbounds/get/{inbound_id}")
        response.raise_for_status()
        body = self._validated_body(response)
        obj = body.get("obj")
        if not isinstance(obj, dict):
            raise RuntimeError("XUI inbound response is missing object payload")
        return obj


def inbound_metrics(inbound: dict[str, Any]) -> tuple[int, int, int]:
    clients = _configured_clients(inbound)
    active_clients = sum(1 for client in clients if client.get("enable", True) is not False)
    stats = inbound.get("clientStats") or []
    if not isinstance(stats, list):
        raise RuntimeError("XUI inbound clientStats is invalid")
    rx_bytes = 0
    tx_bytes = 0
    for stat in stats:
        if not isinstance(stat, dict):
            raise RuntimeError("XUI inbound clientStats entry is invalid")
        rx_bytes += _nonnegative_int(stat.get("up", 0))
        tx_bytes += _nonnegative_int(stat.get("down", 0))
    return active_clients, rx_bytes, tx_bytes


def _configured_clients(inbound: dict[str, Any]) -> list[dict[str, Any]]:
    settings = inbound.get("settings")
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except json.JSONDecodeError as exc:
            raise RuntimeError("XUI inbound settings is invalid JSON") from exc
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise RuntimeError("XUI inbound settings is invalid")
    clients = settings.get("clients") or []
    if not isinstance(clients, list) or any(not isinstance(item, dict) for item in clients):
        raise RuntimeError("XUI inbound clients is invalid")
    return clients


def _inbound_contains_uuid(inbound: dict[str, Any], client_uuid: str) -> bool:
    return any(str(client.get("id") or "") == client_uuid for client in _configured_clients(inbound))


def _nonnegative_int(value: Any) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("XUI traffic metric is invalid") from exc
    if result < 0:
        raise RuntimeError("XUI traffic metric cannot be negative")
    return result


def _expiry_milliseconds(value: datetime | str | None) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("Invalid XUI client expiry timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("XUI client expiry must be timezone-aware")
    return int(value.astimezone(UTC).timestamp() * 1000)


def inbound_numeric_id(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Inbound id must be numeric for XUI runtime: {raw!r}") from exc
    if value <= 0:
        raise RuntimeError("XUI inbound id must be positive")
    return value
