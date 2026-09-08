from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from shop_bot.domain.errors import DomainValidationError, InvalidStateTransition


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass(slots=True)
class SubscriptionPeriod:
    id: int | None
    subscription_id: int | None
    starts_at: datetime
    expires_at: datetime
    is_paid: bool
    created_at: datetime
    payment_order_id: int | None = None

    def __post_init__(self) -> None:
        if self.starts_at >= self.expires_at:
            raise DomainValidationError("Subscription period must have a positive duration")

    def contains(self, moment: datetime) -> bool:
        return self.is_paid and self.starts_at <= moment < self.expires_at


@dataclass(slots=True)
class Subscription:
    id: int | None
    user_id: int
    tariff_id: int
    status: SubscriptionStatus
    created_at: datetime
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        self.status = SubscriptionStatus(self.status)
        if self.user_id <= 0 or self.tariff_id <= 0:
            raise DomainValidationError("Subscription references must be positive")
        if self.status is SubscriptionStatus.ACTIVE and self.ended_at is not None:
            raise DomainValidationError("Active subscription cannot have ended_at")

    @classmethod
    def start(cls, *, user_id: int, tariff_id: int, now: datetime) -> Subscription:
        return cls(
            id=None,
            user_id=user_id,
            tariff_id=tariff_id,
            status=SubscriptionStatus.ACTIVE,
            created_at=now,
        )

    def activate(self) -> None:
        if self.status is SubscriptionStatus.CANCELLED:
            raise InvalidStateTransition("Cancelled subscription cannot be activated")
        self.status = SubscriptionStatus.ACTIVE
        self.ended_at = None

    def expire(self, now: datetime) -> None:
        if self.status is SubscriptionStatus.ENDED:
            return
        self.status = SubscriptionStatus.ENDED
        self.ended_at = now

    def cancel(self, now: datetime) -> None:
        if self.status is SubscriptionStatus.ENDED:
            raise InvalidStateTransition("Ended subscription cannot be cancelled")
        self.status = SubscriptionStatus.CANCELLED
        self.ended_at = now

    def pause(self) -> None:
        if self.status is not SubscriptionStatus.ACTIVE:
            raise InvalidStateTransition("Only active subscription can be paused")
        self.status = SubscriptionStatus.PAUSED

    def is_active(self, *, now: datetime, period: SubscriptionPeriod | None) -> bool:
        return self.status is SubscriptionStatus.ACTIVE and period is not None and period.contains(now)

    def extend(
        self,
        *,
        period_days: int,
        now: datetime,
        last_period: SubscriptionPeriod | None,
        is_paid: bool = True,
        payment_order_id: int | None = None,
    ) -> SubscriptionPeriod:
        if period_days <= 0:
            raise DomainValidationError("Extension duration must be positive")
        if self.status is not SubscriptionStatus.ACTIVE:
            self.activate()
        starts_at = max(now, last_period.expires_at) if last_period is not None else now
        return SubscriptionPeriod(
            id=None,
            subscription_id=self.id,
            starts_at=starts_at,
            expires_at=starts_at + timedelta(days=period_days),
            is_paid=is_paid,
            created_at=now,
            payment_order_id=payment_order_id,
        )
