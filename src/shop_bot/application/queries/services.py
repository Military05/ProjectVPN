from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from shop_bot.application.ports import UnitOfWorkFactory
from shop_bot.domain.vpn.builder import VlessBuildInput, VlessUriBuilder


@dataclass(slots=True)
class BotQueryService:
    uow_factory: UnitOfWorkFactory
    builder: VlessUriBuilder
    clock: Callable[[], datetime]

    async def list_tariffs(self) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            return [dict(row) for row in await uow.admin.list_tariffs(include_disabled=False)]

    async def get_dashboard(self, *, telegram_id: int) -> dict[str, Any]:
        now = self.clock()
        async with self.uow_factory() as uow:
            user = await uow.users.get_user_by_contact("telegram_id", str(telegram_id))
            if user is None:
                return {"user": None, "subscription": None, "vpn_configuration": None}
            user_id = int(user["user_id"])
            subscription = await uow.subscriptions.get_current_access_for_user(user_id, now)
            vpn = await uow.vpn.get_active_configuration_for_user(user_id, now)
        return {
            "user": {"user_id": user_id, "name_or_nick": str(user["name_or_nick"])},
            "subscription": self._subscription_payload(subscription),
            "vpn_configuration": self._vpn_payload(vpn),
        }

    @staticmethod
    def _subscription_payload(subscription: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not subscription:
            return None
        return {
            "subscription_id": int(subscription["subscription_id"]),
            "tariff_id": int(subscription["tariff_id"]),
            "tariff_name": str(subscription["tariff_name"]),
            "status": str(subscription["status"]),
            "starts_at": subscription["starts_at"],
            "expires_at": subscription["expires_at"],
        }

    def _vpn_payload(self, vpn: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not vpn:
            return None
        if str(vpn.get("protocol") or "").strip().lower() != "vless":
            return None
        uri = self.builder.build(
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
        return {
            "vpn_configuration_id": int(vpn["vpn_configuration_id"]),
            "status": str(vpn["status"]),
            "display_name": str(vpn["display_name"]),
            "client_uuid": str(vpn["client_uuid"]),
            "server_name": str(vpn["server_name"]),
            "host": str(vpn["host"]),
            "port": int(vpn["port"]),
            "uri": uri,
        }


@dataclass(slots=True)
class NodeQueryService:
    uow_factory: UnitOfWorkFactory

    async def list_nodes(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            return [dict(row) for row in await uow.nodes.list_nodes(limit=limit, offset=offset)]

    async def list_tasks(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            return [dict(row) for row in await uow.nodes.list_tasks(limit=limit, offset=offset)]
