from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from shop_bot.domain.entities.subscription import Subscription, SubscriptionPeriod
from shop_bot.domain.services.subscription_policy import SubscriptionPolicy


@dataclass(slots=True)
class SubscriptionActivationResult:
    subscription_id: int
    subscription_period_id: int
    starts_at: datetime
    expires_at: datetime
    created_new_subscription: bool


class SubscriptionService:
    """Compatibility facade over the rich-domain SubscriptionPolicy.

    New application code uses ActivateSubscription. This facade preserves the
    public API used by older extensions and tests without duplicating rules.
    """

    def __init__(self, policy: SubscriptionPolicy | None = None) -> None:
        self._policy = policy or SubscriptionPolicy()

    async def apply_paid_period(
        self,
        *,
        subscription_repo: Any,
        user_id: int,
        tariff_id: int,
        period_days: int,
        now: datetime,
    ) -> SubscriptionActivationResult:
        active_row = await subscription_repo.lock_active_subscription_for_user(user_id)
        active = self._subscription_from_legacy_row(active_row, now) if active_row is not None else None
        last_row = None
        if active is not None and active.tariff_id == tariff_id and active.id is not None:
            last_row = await subscription_repo.lock_last_period(active.id)
        last_period = self._period_from_legacy_row(last_row, active.id if active else None, now)

        activation = self._policy.apply_paid_period(
            active_subscription=active,
            last_period=last_period,
            user_id=user_id,
            tariff_id=tariff_id,
            period_days=period_days,
            now=now,
        )
        if activation.replaced_subscription is not None and activation.replaced_subscription.id is not None:
            await subscription_repo.end_subscription(
                activation.replaced_subscription.id,
                ended_at=activation.replaced_subscription.ended_at,
                status=str(activation.replaced_subscription.status),
            )
        if activation.created_new_subscription:
            subscription_id = await subscription_repo.create_subscription(
                user_id=activation.subscription.user_id,
                tariff_id=activation.subscription.tariff_id,
                status=str(activation.subscription.status),
                created_at=activation.subscription.created_at,
            )
        else:
            if activation.subscription.id is None:
                raise RuntimeError("Existing subscription has no id")
            subscription_id = activation.subscription.id

        period_id = await subscription_repo.create_period(
            subscription_id=subscription_id,
            starts_at=activation.period.starts_at,
            expires_at=activation.period.expires_at,
            is_paid=activation.period.is_paid,
            created_at=activation.period.created_at,
        )
        return SubscriptionActivationResult(
            subscription_id=subscription_id,
            subscription_period_id=period_id,
            starts_at=activation.period.starts_at,
            expires_at=activation.period.expires_at,
            created_new_subscription=activation.created_new_subscription,
        )

    @staticmethod
    def _subscription_from_legacy_row(row: Any, now: datetime) -> Subscription:
        return Subscription(
            id=int(row["subscription_id"]),
            user_id=int(row.get("user_id", 1)),
            tariff_id=int(row["tariff_id"]),
            status=str(row.get("status", "active")),
            created_at=row.get("created_at", now),
            ended_at=row.get("ended_at"),
        )

    @staticmethod
    def _period_from_legacy_row(row: Any, subscription_id: int | None, now: datetime) -> SubscriptionPeriod | None:
        if row is None:
            return None
        expires_at = row["expires_at"]
        return SubscriptionPeriod(
            id=int(row["subscription_period_id"]) if row.get("subscription_period_id") is not None else None,
            subscription_id=subscription_id,
            starts_at=row.get("starts_at", expires_at - timedelta(seconds=1)),
            expires_at=expires_at,
            is_paid=bool(row.get("is_paid", True)),
            created_at=row.get("created_at", now),
        )
