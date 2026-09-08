from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from shop_bot.apps.bot.order_flow import OrderFlow, TariffUnavailableError
from shop_bot.core.config import Settings
from shop_bot.schemas.bot import BotCreateOrderResponse, TariffResponse


def _tariff(*, tariff_id: int = 7, enabled: bool = True) -> TariffResponse:
    return TariffResponse(
        tariff_id=tariff_id,
        tariff_name="Current backend name",
        description="Current backend description",
        price_minor=12900,
        currency="RUB",
        period_days=30,
        is_enabled=enabled,
    )


def _order() -> BotCreateOrderResponse:
    return BotCreateOrderResponse(
        payment_order_id=42,
        status="pending",
        provider="yookassa",
        provider_payment_id="provider-42",
        payment_url="https://payments.example/order/42",
        amount_minor=12900,
        currency="RUB",
        tariff_name="Current backend name",
    )


class BackendStub:
    def __init__(self, tariffs: list[TariffResponse]) -> None:
        self.tariffs = tariffs
        self.create_calls: list[dict[str, Any]] = []

    async def list_tariffs(self) -> list[TariffResponse]:
        return self.tariffs

    async def create_order(self, **payload: Any) -> BotCreateOrderResponse:
        self.create_calls.append(payload)
        return _order()


@pytest.mark.asyncio
async def test_retry_survives_bot_restart_and_reuses_durable_identity() -> None:
    backend = BackendStub([_tariff()])

    first_process = OrderFlow(backend=backend, provider="yookassa")
    restarted_process = OrderFlow(backend=backend, provider="yookassa")

    first = await first_process.create_or_retry(
        telegram_id=100,
        tariff_id=7,
        intent_id="Abc_123-xy",
        username="alice",
        name_or_nick="Alice",
    )
    retried = await restarted_process.create_or_retry(
        telegram_id=100,
        tariff_id=7,
        intent_id="Abc_123-xy",
        username="alice",
        name_or_nick="Alice",
    )

    assert first.order == retried.order
    assert [call["idempotency_key"] for call in backend.create_calls] == [
        "tg-ui2:100:7:Abc_123-xy",
        "tg-ui2:100:7:Abc_123-xy",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("tariffs", [[], [_tariff(enabled=False)], [_tariff(tariff_id=8)]])
async def test_order_flow_refetches_and_rejects_unavailable_tariff(
    tariffs: list[TariffResponse],
) -> None:
    backend = BackendStub(tariffs)
    flow = OrderFlow(backend=backend, provider="yookassa")

    with pytest.raises(TariffUnavailableError):
        await flow.create_or_retry(
            telegram_id=100,
            tariff_id=7,
            intent_id="Abc_123-xy",
            username=None,
            name_or_nick="Alice",
        )

    assert backend.create_calls == []


def test_bot_controller_has_no_process_local_order_intent_state() -> None:
    from shop_bot.apps.bot import handlers

    source = handlers.__loader__.get_source(handlers.__name__)  # type: ignore[union-attr]
    assert source is not None
    assert "_order_intents" not in source
    assert "OrderIntentContext" not in source


def test_bot_dedup_ttl_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="bot_dedup_ttl_seconds must be positive"):
        Settings(bot_dedup_ttl_seconds=0)
