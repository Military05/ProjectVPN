from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from shop_bot.core.config import Settings
from shop_bot.domain.entities.payment import PaymentAttempt, PaymentEvent, PaymentEventStatus, PaymentOrder, PaymentStatus
from shop_bot.domain.errors import PaymentInvariantViolation
from shop_bot.domain.services.payment_settlement import validate_paid_settlement
from shop_bot.domain.value_objects.money import Money
from shop_bot.infrastructure.payments.yookassa import YooKassaAdapter


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "payments" / "yookassa"
PROVIDER_PAYMENT_ID = "2f3f7d9a-000f-5000-9000-1d6f5e8a2c10"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class HttpBoundary:
    def __init__(self, *, post_payload: dict[str, Any] | None = None, get_payload: dict[str, Any] | None = None) -> None:
        self.post_payload = post_payload
        self.get_payload = get_payload
        self.posts: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.gets: list[tuple[str, dict[str, str]]] = []

    def client_type(self):
        boundary = self

        class Client:
            def __init__(self, **kwargs: Any) -> None:
                del kwargs

            async def __aenter__(self) -> "Client":
                return self

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                return None

            async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> FakeResponse:
                boundary.posts.append((url, json, headers))
                assert boundary.post_payload is not None
                return FakeResponse(boundary.post_payload)

            async def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
                boundary.gets.append((url, headers))
                assert boundary.get_payload is not None
                return FakeResponse(boundary.get_payload)

        return Client


def settings() -> Settings:
    return Settings(
        yookassa_shop_id="shop-123",
        yookassa_secret_key="secret-456",
        yookassa_return_url="https://merchant.example/payments/return",
    )


def order_entity() -> PaymentOrder:
    return PaymentOrder(
        id=22,
        user_id=7,
        tariff_id=8,
        provider="yookassa",
        status=PaymentStatus.PENDING,
        amount=Money(9900, "RUB"),
        requested_period_days=30,
        idempotency_key="idem-yookassa-order-22",
    )


def gateway_order(order: PaymentOrder) -> dict[str, Any]:
    return {
        "payment_order_id": order.id,
        "user_id": order.user_id,
        "tariff_id": order.tariff_id,
        "provider": order.provider,
        "status": str(order.status),
        "amount_minor": order.amount.minor,
        "currency": order.amount.currency,
        "requested_period_days": order.requested_period_days,
        "idempotency_key": order.idempotency_key,
        "metadata": order.metadata,
    }


def settlement_entities(normalized: Any, intent: Any) -> tuple[PaymentEvent, PaymentAttempt, PaymentOrder]:
    order = order_entity()
    attempt = PaymentAttempt(
        id=33,
        payment_order_id=22,
        provider="yookassa",
        status=PaymentStatus(intent.status),
        provider_payment_id=intent.provider_payment_id,
        payment_url=intent.payment_url,
        payload=intent.payload,
    )
    event = PaymentEvent(
        id=44,
        payment_order_id=normalized.payment_order_id,
        payment_attempt_id=33,
        provider=normalized.provider,
        event_type=normalized.event_type,
        event_key=normalized.event_key,
        status=PaymentEventStatus.RECEIVED,
        payload=normalized.payload,
        occurred_at=normalized.occurred_at,
        provider_payment_id=normalized.provider_payment_id,
        reported_payment_order_id=normalized.payment_order_id,
        payment_status=PaymentStatus(normalized.status),
        amount_minor=normalized.amount_minor,
        currency=normalized.currency,
    )
    return event, attempt, order


@pytest.mark.asyncio
async def test_yookassa_real_contract_create_webhook_normalization_and_settlement(monkeypatch: pytest.MonkeyPatch) -> None:
    boundary = HttpBoundary(post_payload=fixture("create_response.json"), get_payload=fixture("authoritative_succeeded.json"))
    monkeypatch.setattr(httpx, "AsyncClient", boundary.client_type())
    adapter = YooKassaAdapter(settings())
    order = order_entity()

    intent = await adapter.create_payment(order=gateway_order(order), return_url="https://ignored.example")
    assert len(boundary.posts) == 1
    url, payload, headers = boundary.posts[0]
    assert url == "https://api.yookassa.ru/v3/payments"
    assert headers["Authorization"].startswith("Basic ")
    assert headers["Idempotence-Key"] == order.idempotency_key
    assert payload["amount"] == {"value": "99.00", "currency": "RUB"}
    assert payload["metadata"]["payment_order_id"] == 22
    assert intent.provider == "yookassa"
    assert intent.status == "pending"
    assert intent.provider_payment_id == PROVIDER_PAYMENT_ID
    assert intent.payment_url is not None

    normalized = await adapter.verify_and_normalize_webhook(
        raw_body=json.dumps(fixture("webhook_succeeded.json")).encode(), headers={}
    )
    assert boundary.gets[0][0] == f"https://api.yookassa.ru/v3/payments/{PROVIDER_PAYMENT_ID}"
    assert normalized.provider == "yookassa"
    assert normalized.status == "paid"
    assert normalized.provider_payment_id == PROVIDER_PAYMENT_ID
    assert normalized.payment_order_id == 22
    assert normalized.amount_minor == 9900
    assert normalized.currency == "RUB"
    assert normalized.occurred_at.isoformat().startswith("2026-08-19T09:31:12.123")

    event, attempt, payment_order = settlement_entities(normalized, intent)
    validate_paid_settlement(event=event, attempt=attempt, order=payment_order)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authoritative_fixture",
    ["authoritative_amount_mismatch.json", "authoritative_currency_mismatch.json"],
)
async def test_yookassa_authoritative_mismatch_reaches_real_settlement_validator(
    monkeypatch: pytest.MonkeyPatch, authoritative_fixture: str
) -> None:
    boundary = HttpBoundary(post_payload=fixture("create_response.json"), get_payload=fixture(authoritative_fixture))
    monkeypatch.setattr(httpx, "AsyncClient", boundary.client_type())
    adapter = YooKassaAdapter(settings())
    intent = await adapter.create_payment(order=gateway_order(order_entity()), return_url="https://ignored.example")
    normalized = await adapter.verify_and_normalize_webhook(
        raw_body=json.dumps(fixture("webhook_succeeded.json")).encode(), headers={}
    )
    event, attempt, payment_order = settlement_entities(normalized, intent)
    with pytest.raises(PaymentInvariantViolation):
        validate_paid_settlement(event=event, attempt=attempt, order=payment_order)


@pytest.mark.asyncio
async def test_yookassa_does_not_trust_succeeded_inbound_when_authoritative_status_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = HttpBoundary(get_payload=fixture("authoritative_pending.json"))
    monkeypatch.setattr(httpx, "AsyncClient", boundary.client_type())
    normalized = await YooKassaAdapter(settings()).verify_and_normalize_webhook(
        raw_body=json.dumps(fixture("webhook_succeeded.json")).encode(), headers={}
    )
    assert normalized.status == "pending"
    assert normalized.amount_minor == 9900
    assert normalized.currency == "RUB"
