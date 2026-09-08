from __future__ import annotations

import httpx

from shop_bot.core.config import Settings
from shop_bot.schemas.bot import BotCreateOrderResponse, BotDashboardResponse, BotUserResponse, TariffResponse


class BackendApiClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=str(settings.backend_base_url).rstrip("/"),
            timeout=settings.request_timeout_seconds,
            headers={"X-Internal-Api-Key": settings.internal_api_key},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def register_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        name_or_nick: str,
    ) -> BotUserResponse:
        response = await self._client.post(
            "/bot/register",
            json={
                "telegram_id": telegram_id,
                "username": username,
                "name_or_nick": name_or_nick,
            },
        )
        response.raise_for_status()
        return BotUserResponse.model_validate(response.json())

    async def list_tariffs(self) -> list[TariffResponse]:
        response = await self._client.get("/bot/tariffs")
        response.raise_for_status()
        return [TariffResponse.model_validate(item) for item in response.json()]

    async def create_order(
        self,
        *,
        telegram_id: int,
        tariff_id: int,
        provider: str | None,
        idempotency_key: str,
        username: str | None,
        name_or_nick: str | None,
    ) -> BotCreateOrderResponse:
        response = await self._client.post(
            "/bot/orders",
            json={
                "telegram_id": telegram_id,
                "tariff_id": tariff_id,
                "provider": provider,
                "idempotency_key": idempotency_key,
                "username": username,
                "name_or_nick": name_or_nick,
            },
        )
        response.raise_for_status()
        return BotCreateOrderResponse.model_validate(response.json())

    async def get_dashboard(self, *, telegram_id: int) -> BotDashboardResponse:
        response = await self._client.get("/bot/dashboard", params={"telegram_id": telegram_id})
        response.raise_for_status()
        return BotDashboardResponse.model_validate(response.json())
