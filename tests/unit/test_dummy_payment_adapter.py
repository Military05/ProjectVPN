import json

import pytest

from shop_bot.core.config import Settings
from shop_bot.infrastructure.payments.dummy import DummyPaymentAdapter


async def test_dummy_payment_adapter_creates_payment_url() -> None:
    settings = Settings(dummy_payment_base_url="http://localhost:8080")
    adapter = DummyPaymentAdapter(settings)

    intent = await adapter.create_payment(
        order={"payment_order_id": 42},
        return_url="http://localhost:8080/docs",
    )

    assert intent.provider == "dummy"
    assert intent.status == "pending"
    assert "/sandbox/payments/42" in intent.payment_url
    assert intent.provider_payment_id.startswith("dummy-42-")


@pytest.mark.asyncio
async def test_dummy_payment_adapter_normalizes_webhook() -> None:
    settings = Settings(dummy_payment_base_url="http://localhost:8080")
    adapter = DummyPaymentAdapter(settings)

    payload = {
        "payment_order_id": 42,
        "provider_payment_id": "dummy-42-test",
        "status": "paid",
        "amount_minor": 9900,
        "currency": "RUB",
    }
    event = await adapter.verify_and_normalize_webhook(
        raw_body=json.dumps(payload).encode(),
        headers={},
    )

    assert event.provider == "dummy"
    assert event.payment_order_id == 42
    assert event.provider_payment_id == "dummy-42-test"
    assert event.status == "paid"
    assert event.amount_minor == 9900
    assert event.currency == "RUB"
