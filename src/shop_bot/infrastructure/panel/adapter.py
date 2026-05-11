from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from shop_bot.core.config import Settings


class PanelAdapter(Protocol):
    async def provision(self, payload: dict[str, Any]) -> None:
        ...

    async def revoke(self, payload: dict[str, Any]) -> None:
        ...


@dataclass(slots=True)
class StubPanelAdapter:
    async def provision(self, payload: dict[str, Any]) -> None:
        return None

    async def revoke(self, payload: dict[str, Any]) -> None:
        return None


@dataclass(slots=True)
class XuiPanelAdapter:
    settings: Settings

    async def provision(self, payload: dict[str, Any]) -> None:
        if not self.settings.xui_base_url or not self.settings.xui_username or not self.settings.xui_password:
            raise RuntimeError("XUI credentials are not configured")
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            login_response = await client.post(
                f"{self.settings.xui_base_url.rstrip('/')}/login",
                data={"username": self.settings.xui_username, "password": self.settings.xui_password},
            )
            login_response.raise_for_status()
            response = await client.post(
                f"{self.settings.xui_base_url.rstrip('/')}/panel/api/inbounds/addClient",
                json={
                    "id": payload["xui_inbound_id"],
                    "settings": {
                        "clients": [
                            {
                                "id": payload["client_uuid"],
                                "email": payload["display_name"],
                                "flow": payload.get("flow"),
                            }
                        ]
                    },
                },
            )
            response.raise_for_status()

    async def revoke(self, payload: dict[str, Any]) -> None:
        if not self.settings.xui_base_url or not self.settings.xui_username or not self.settings.xui_password:
            raise RuntimeError("XUI credentials are not configured")
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            login_response = await client.post(
                f"{self.settings.xui_base_url.rstrip('/')}/login",
                data={"username": self.settings.xui_username, "password": self.settings.xui_password},
            )
            login_response.raise_for_status()
            response = await client.post(
                f"{self.settings.xui_base_url.rstrip('/')}/panel/api/inbounds/delClient/{payload['client_uuid']}",
                json={"id": payload["xui_inbound_id"]},
            )
            response.raise_for_status()


def build_panel_adapter(settings: Settings) -> PanelAdapter:
    if settings.panel_mode == "xui":
        return XuiPanelAdapter(settings=settings)
    return StubPanelAdapter()
