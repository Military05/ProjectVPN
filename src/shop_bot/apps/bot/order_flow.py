from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shop_bot.schemas.bot import BotCreateOrderResponse, TariffResponse


class TariffUnavailableError(LookupError):
    """Raised when a callback references a missing or disabled tariff."""


class OrderBackend(Protocol):
    async def list_tariffs(self) -> list[TariffResponse]: ...

    async def create_order(
        self,
        *,
        telegram_id: int,
        tariff_id: int,
        provider: str | None,
        idempotency_key: str,
        username: str | None,
        name_or_nick: str | None,
    ) -> BotCreateOrderResponse: ...


@dataclass(frozen=True, slots=True)
class OrderFlowResult:
    tariff: TariffResponse
    order: BotCreateOrderResponse


class OrderFlow:
    """Create or retry an order using only durable callback identity."""

    def __init__(self, *, backend: OrderBackend, provider: str) -> None:
        self._backend = backend
        self._provider = provider

    async def create_or_retry(
        self,
        *,
        telegram_id: int,
        tariff_id: int,
        intent_id: str,
        username: str | None,
        name_or_nick: str,
    ) -> OrderFlowResult:
        tariffs = await self._backend.list_tariffs()
        tariff = next(
            (
                item
                for item in tariffs
                if item.tariff_id == tariff_id and item.is_enabled
            ),
            None,
        )
        if tariff is None:
            raise TariffUnavailableError(tariff_id)

        order = await self._backend.create_order(
            telegram_id=telegram_id,
            tariff_id=tariff_id,
            provider=self._provider,
            idempotency_key=f"tg-ui2:{telegram_id}:{tariff_id}:{intent_id}",
            username=username,
            name_or_nick=name_or_nick,
        )
        return OrderFlowResult(tariff=tariff, order=order)
