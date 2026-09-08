from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from shop_bot.domain.entities.subscription import Subscription, SubscriptionPeriod


@dataclass(frozen=True, slots=True)
class SubscriptionActivation:
    subscription: Subscription
    period: SubscriptionPeriod
    created_new_subscription: bool
    replaced_subscription: Subscription | None = None


class SubscriptionPolicy:
    """Pure domain policy for applying a paid tariff period."""

    def apply_paid_period(
        self,
        *,
        active_subscription: Subscription | None,
        last_period: SubscriptionPeriod | None,
        user_id: int,
        tariff_id: int,
        period_days: int,
        now: datetime,
    ) -> SubscriptionActivation:
        replaced: Subscription | None = None
        created_new = active_subscription is None

        if active_subscription is None:
            subscription = Subscription.start(user_id=user_id, tariff_id=tariff_id, now=now)
        elif active_subscription.tariff_id != tariff_id:
            active_subscription.expire(now)
            replaced = active_subscription
            subscription = Subscription.start(user_id=user_id, tariff_id=tariff_id, now=now)
            last_period = None
            created_new = True
        else:
            subscription = active_subscription

        period = subscription.extend(
            period_days=period_days,
            now=now,
            last_period=last_period,
            is_paid=True,
        )
        return SubscriptionActivation(
            subscription=subscription,
            period=period,
            created_new_subscription=created_new,
            replaced_subscription=replaced,
        )
