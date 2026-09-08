from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from shop_bot.core.config import Settings
from shop_bot.infrastructure.xui import XuiClient, inbound_numeric_id


class PanelAdapter(Protocol):
    async def provision(self, payload: dict[str, Any]) -> None: ...
    async def revoke(self, payload: dict[str, Any]) -> None: ...


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
        client = XuiClient(self.settings)
        await client.add_client(
            inbound_id=inbound_numeric_id(payload["xui_inbound_id"]),
            client_uuid=str(payload["client_uuid"]),
            email=str(payload["display_name"]),
            flow=payload.get("flow"),
            expires_at=payload.get("expires_at"),
        )

    async def revoke(self, payload: dict[str, Any]) -> None:
        client = XuiClient(self.settings)
        deleted = await client.delete_client(
            inbound_id=inbound_numeric_id(payload["xui_inbound_id"]),
            client_uuid=str(payload["client_uuid"]),
        )
        if not deleted:
            raise RuntimeError("XUI client deletion could not be verified")


def build_panel_adapter(settings: Settings) -> PanelAdapter:
    return XuiPanelAdapter(settings=settings) if settings.panel_mode == "xui" else StubPanelAdapter()
