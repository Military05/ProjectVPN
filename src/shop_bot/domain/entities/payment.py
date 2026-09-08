from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from shop_bot.domain.errors import DomainValidationError, InvalidStateTransition
from shop_bot.domain.value_objects.money import Money


class PaymentStatus(StrEnum):
    CREATED = "created"
    PENDING = "pending"
    AUTHORIZED = "authorized"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PaymentEventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"


class PaymentTransitionResult(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    STALE = "stale"


@dataclass(slots=True)
class PaymentOrder:
    id: int | None
    user_id: int
    tariff_id: int
    provider: str
    status: PaymentStatus
    amount: Money
    requested_period_days: int
    idempotency_key: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    paid_at: datetime | None = None

    def __post_init__(self) -> None:
        self.status = PaymentStatus(self.status)
        self.provider = self.provider.strip()
        self.idempotency_key = self.idempotency_key.strip()
        if self.user_id <= 0 or self.tariff_id <= 0:
            raise DomainValidationError("Payment order references must be positive")
        if not self.provider or not self.idempotency_key:
            raise DomainValidationError("Payment provider and idempotency key cannot be blank")
        if self.amount.minor <= 0 or self.requested_period_days <= 0:
            raise DomainValidationError("Payment amount and period must be positive")

    def has_same_creation_identity(self, other: "PaymentOrder") -> bool:
        return (
            self.user_id == other.user_id
            and self.tariff_id == other.tariff_id
            and self.provider == other.provider
            and self.amount.minor == other.amount.minor
            and self.amount.currency == other.amount.currency
            and self.requested_period_days == other.requested_period_days
        )

    def mark_paid(self, now: datetime) -> bool:
        if self.status is PaymentStatus.PAID:
            return False
        if self.status is PaymentStatus.CREATED:
            raise InvalidStateTransition("Created order must become pending before payment")
        self.status = PaymentStatus.PAID
        self.paid_at = now
        self.updated_at = now
        return True

    def cancel(self, now: datetime) -> bool:
        return self._finish(PaymentStatus.CANCELLED, now)

    def fail(self, now: datetime) -> bool:
        return self._finish(PaymentStatus.FAILED, now)

    def expire(self, now: datetime) -> bool:
        return self._finish(PaymentStatus.EXPIRED, now)

    def apply_external_status(self, status: PaymentStatus, now: datetime) -> bool:
        if self.status is PaymentStatus.PAID and status is not PaymentStatus.PAID:
            return False
        if status is PaymentStatus.PAID:
            return self.mark_paid(now)
        if status is PaymentStatus.CANCELLED:
            return self.cancel(now)
        if status is PaymentStatus.FAILED:
            return self.fail(now)
        if status is PaymentStatus.EXPIRED:
            return self.expire(now)
        return False

    def _finish(self, target: PaymentStatus, now: datetime) -> bool:
        if self.status is target:
            return False
        if self.status is PaymentStatus.PAID:
            raise InvalidStateTransition("Paid order cannot transition to a failure state")
        if self.status is not PaymentStatus.PENDING:
            return False
        self.status = target
        self.updated_at = now
        return True


@dataclass(slots=True)
class PaymentAttempt:
    id: int | None
    payment_order_id: int
    provider: str
    status: PaymentStatus
    provider_payment_id: str | None = None
    payment_url: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    creation_claimed_at: datetime | None = None
    creation_lease_expires_at: datetime | None = None
    creation_lease_token: UUID | None = None

    def __post_init__(self) -> None:
        self.status = PaymentStatus(self.status)
        if self.creation_lease_token is not None and not isinstance(self.creation_lease_token, UUID):
            self.creation_lease_token = UUID(str(self.creation_lease_token))

    def claim_creation(self, *, now: datetime, lease_expires_at: datetime, lease_token: UUID) -> None:
        if self.status is not PaymentStatus.CREATED:
            raise InvalidStateTransition("Only created payment attempt can own invoice creation")
        if lease_expires_at <= now:
            raise DomainValidationError("Payment creation lease must expire after claim time")
        self.creation_claimed_at = now
        self.creation_lease_expires_at = lease_expires_at
        self.creation_lease_token = lease_token
        self.updated_at = now

    def creation_claim_is_active(self, now: datetime) -> bool:
        return (
            self.status is PaymentStatus.CREATED
            and self.creation_lease_token is not None
            and self.creation_lease_expires_at is not None
            and now < self.creation_lease_expires_at
        )

    def creation_claim_is_expired(self, now: datetime) -> bool:
        return (
            self.status is PaymentStatus.CREATED
            and self.creation_lease_expires_at is not None
            and self.creation_lease_expires_at <= now
        )

    def owns_creation_claim(self, token: UUID) -> bool:
        return self.status is PaymentStatus.CREATED and self.creation_lease_token == token

    def clear_creation_claim(self) -> None:
        self.creation_claimed_at = None
        self.creation_lease_expires_at = None
        self.creation_lease_token = None

    def finalize_creation(
        self,
        *,
        status: PaymentStatus,
        provider_payment_id: str,
        payment_url: str | None,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
        if self.status is not PaymentStatus.CREATED:
            raise InvalidStateTransition("Only created payment attempt can be finalized")
        final_status = PaymentStatus(status)
        if final_status is PaymentStatus.CREATED:
            raise InvalidStateTransition("Provider result cannot remain in creation-claim state")
        self.status = final_status
        self.provider_payment_id = provider_payment_id
        self.payment_url = payment_url
        self.payload = payload
        self.updated_at = now
        self.clear_creation_claim()

    def fail_creation(self, *, now: datetime, diagnostic: str) -> None:
        if self.status is not PaymentStatus.CREATED:
            return
        self.status = PaymentStatus.FAILED
        self.payload = {**self.payload, "creation_error": diagnostic}
        self.updated_at = now
        self.clear_creation_claim()

    def apply_provider_status(
        self,
        status: PaymentStatus,
        *,
        provider_payment_id: str | None = None,
        payment_url: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> PaymentTransitionResult:
        target = PaymentStatus(status)
        if self.status in {PaymentStatus.PAID, PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELLED}:
            return PaymentTransitionResult.DUPLICATE if self.status is target else PaymentTransitionResult.STALE
        # Statuses are monotonic in the provider lifecycle.
        order = {PaymentStatus.CREATED: 0, PaymentStatus.PENDING: 1, PaymentStatus.AUTHORIZED: 2, PaymentStatus.PAID: 3, PaymentStatus.FAILED: 3, PaymentStatus.EXPIRED: 3, PaymentStatus.CANCELLED: 3}
        if order[target] < order[self.status]:
            return PaymentTransitionResult.STALE
        self.status = target
        if provider_payment_id is not None:
            self.provider_payment_id = provider_payment_id
        if payment_url is not None:
            self.payment_url = payment_url
        if payload is not None:
            self.payload = payload
        return PaymentTransitionResult.APPLIED

    def apply_status(
        self,
        status: PaymentStatus,
        *,
        provider_payment_id: str | None = None,
        payment_url: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Backward-compatible wrapper; new code should inspect the transition result."""
        self.apply_provider_status(status, provider_payment_id=provider_payment_id, payment_url=payment_url, payload=payload)


@dataclass(slots=True)
class PaymentEvent:
    id: int | None
    payment_order_id: int | None
    payment_attempt_id: int | None
    provider: str
    event_type: str
    event_key: str
    status: PaymentEventStatus
    payload: dict[str, Any]
    occurred_at: datetime
    provider_payment_id: str | None = None
    reported_payment_order_id: int | None = None
    payment_status: PaymentStatus | None = None
    amount_minor: int | None = None
    currency: str | None = None
    received_at: datetime | None = None
    processed_at: datetime | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        self.status = PaymentEventStatus(self.status)
        if self.payment_status is not None:
            self.payment_status = PaymentStatus(self.payment_status)
        if self.currency is not None:
            normalized_currency = self.currency.strip().upper()
            self.currency = normalized_currency or None

    def normalized_payment_status(self) -> PaymentStatus:
        if self.payment_status is not None:
            return self.payment_status
        return self.infer_payment_status(event_type=self.event_type, payload=self.payload)

    @staticmethod
    def infer_payment_status(*, event_type: str, payload: dict[str, Any]) -> PaymentStatus:
        raw_status = payload.get("status") or payload.get("payment_status") or payload.get("state")
        if isinstance(raw_status, str):
            lowered = raw_status.lower()
            aliases = {
                "paid": PaymentStatus.PAID,
                "succeeded": PaymentStatus.PAID,
                "success": PaymentStatus.PAID,
                "completed": PaymentStatus.PAID,
                "failed": PaymentStatus.FAILED,
                "error": PaymentStatus.FAILED,
                "expired": PaymentStatus.EXPIRED,
                "cancelled": PaymentStatus.CANCELLED,
                "canceled": PaymentStatus.CANCELLED,
            }
            if lowered in aliases:
                return aliases[lowered]
        lowered_type = event_type.lower()
        if any(token in lowered_type for token in ("paid", "succeeded", "completed")):
            return PaymentStatus.PAID
        if "cancel" in lowered_type:
            return PaymentStatus.CANCELLED
        if "expire" in lowered_type:
            return PaymentStatus.EXPIRED
        if "fail" in lowered_type or "error" in lowered_type:
            return PaymentStatus.FAILED
        return PaymentStatus.PENDING

    def mark_processed(self, now: datetime) -> None:
        self.status = PaymentEventStatus.PROCESSED
        self.processed_at = now
        self.error_message = None

    def ignore(self, now: datetime, error: str | None = None) -> None:
        self.status = PaymentEventStatus.IGNORED
        self.processed_at = now
        if error is not None:
            self.error_message = error

    def fail(self, now: datetime, error: str) -> None:
        self.status = PaymentEventStatus.FAILED
        self.processed_at = now
        self.error_message = error


@dataclass(slots=True)
class PaymentTransaction:
    provider: str
    external_id: str
    status: PaymentStatus
    amount: Money
    transaction_type: str
    payload: dict[str, Any] = field(default_factory=dict)
