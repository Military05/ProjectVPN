from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shop_bot.domain.entities.payment import (
    PaymentAttempt,
    PaymentEvent,
    PaymentEventStatus,
    PaymentOrder,
    PaymentStatus,
)
from shop_bot.domain.errors import PaymentInvariantViolation
from shop_bot.domain.services.payment_settlement import validate_paid_settlement
from shop_bot.domain.value_objects.money import Money


NOW = datetime(2026, 8, 19, tzinfo=UTC)


def matching_entities() -> tuple[PaymentEvent, PaymentAttempt, PaymentOrder]:
    order = PaymentOrder(
        id=22,
        user_id=1,
        tariff_id=2,
        provider="cryptobot",
        status=PaymentStatus.PENDING,
        amount=Money(9900, "RUB"),
        requested_period_days=30,
        idempotency_key="order-22",
    )
    attempt = PaymentAttempt(
        id=33,
        payment_order_id=22,
        provider="cryptobot",
        status=PaymentStatus.PENDING,
        provider_payment_id="pay-abc",
    )
    event = PaymentEvent(
        id=44,
        payment_order_id=22,
        payment_attempt_id=33,
        provider="cryptobot",
        event_type="invoice_paid",
        event_key="evt-44",
        status=PaymentEventStatus.RECEIVED,
        payload={"status": "paid"},
        occurred_at=NOW,
        provider_payment_id="pay-abc",
        reported_payment_order_id=22,
        payment_status=PaymentStatus.PAID,
        amount_minor=9900,
        currency="rub",
    )
    return event, attempt, order


def test_matching_paid_settlement_passes() -> None:
    event, attempt, order = matching_entities()
    validate_paid_settlement(event=event, attempt=attempt, order=order)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda event, attempt, order: setattr(event, "provider", "heleket"),
        lambda event, attempt, order: setattr(attempt, "provider", "heleket"),
        lambda event, attempt, order: setattr(event, "provider_payment_id", "other"),
        lambda event, attempt, order: setattr(event, "provider_payment_id", None),
        lambda event, attempt, order: setattr(attempt, "provider_payment_id", None),
        lambda event, attempt, order: setattr(attempt, "payment_order_id", 999),
        lambda event, attempt, order: setattr(event, "payment_order_id", 999),
        lambda event, attempt, order: setattr(event, "reported_payment_order_id", 999),
        lambda event, attempt, order: setattr(event, "amount_minor", 9899),
        lambda event, attempt, order: setattr(event, "amount_minor", None),
        lambda event, attempt, order: setattr(event, "currency", "USD"),
        lambda event, attempt, order: setattr(event, "currency", None),
    ],
)
def test_paid_settlement_rejects_each_invariant_violation(mutation) -> None:
    event, attempt, order = matching_entities()
    mutation(event, attempt, order)
    with pytest.raises(PaymentInvariantViolation):
        validate_paid_settlement(event=event, attempt=attempt, order=order)


def test_paid_settlement_requires_exact_attempt() -> None:
    event, _, order = matching_entities()
    with pytest.raises(PaymentInvariantViolation):
        validate_paid_settlement(event=event, attempt=None, order=order)
