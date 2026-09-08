from __future__ import annotations

from shop_bot.domain.entities.payment import PaymentAttempt, PaymentEvent, PaymentOrder
from shop_bot.domain.errors import PaymentInvariantViolation


def validate_paid_settlement(
    *,
    event: PaymentEvent,
    attempt: PaymentAttempt | None,
    order: PaymentOrder,
) -> None:
    if attempt is None:
        raise PaymentInvariantViolation("exact payment attempt is required")
    if order.id is None:
        raise PaymentInvariantViolation("persisted payment order id is required")
    if event.provider != order.provider or event.provider != attempt.provider:
        raise PaymentInvariantViolation("payment provider mismatch")
    if not event.provider_payment_id or not attempt.provider_payment_id:
        raise PaymentInvariantViolation("provider payment id is required")
    if event.provider_payment_id != attempt.provider_payment_id:
        raise PaymentInvariantViolation("provider payment id mismatch")
    if attempt.payment_order_id != order.id:
        raise PaymentInvariantViolation("payment attempt order mismatch")
    if event.payment_order_id != attempt.payment_order_id:
        raise PaymentInvariantViolation("payment event order mismatch")
    if (
        event.reported_payment_order_id is not None
        and event.reported_payment_order_id != attempt.payment_order_id
    ):
        raise PaymentInvariantViolation("provider-reported order mismatch")
    if event.amount_minor is None:
        raise PaymentInvariantViolation("verified payment amount is required")
    if event.amount_minor != order.amount.minor:
        raise PaymentInvariantViolation("payment amount mismatch")
    if event.currency is None or not event.currency.strip():
        raise PaymentInvariantViolation("verified payment currency is required")
    if event.currency.strip().upper() != order.amount.currency.strip().upper():
        raise PaymentInvariantViolation("payment currency mismatch")
