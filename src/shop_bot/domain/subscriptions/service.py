from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(slots=True)
class SubscriptionActivationResult:
    subscription_id: int
    subscription_period_id: int
    starts_at: datetime
    expires_at: datetime
    created_new_subscription: bool


class SubscriptionService:
    async def apply_paid_period(
        self,
        *,
        subscription_repo: Any,
        user_id: int,
        tariff_id: int,
        period_days: int,
        now: datetime,
    ) -> SubscriptionActivationResult:
        active_subscription = await subscription_repo.lock_active_subscription_for_user(user_id)
        created_new_subscription = False

        if active_subscription is None:
            subscription_id = await subscription_repo.create_subscription(
                user_id=user_id,
                tariff_id=tariff_id,
                status="active",
                created_at=now,
            )
            starts_at = now
            created_new_subscription = True
        else:
            subscription_id = int(active_subscription["subscription_id"])
            active_tariff_id = int(active_subscription["tariff_id"])
            if active_tariff_id != tariff_id:
                await subscription_repo.end_subscription(subscription_id, ended_at=now, status="ended")
                subscription_id = await subscription_repo.create_subscription(
                    user_id=user_id,
                    tariff_id=tariff_id,
                    status="active",
                    created_at=now,
                )
                starts_at = now
                created_new_subscription = True
            else:
                last_period = await subscription_repo.lock_last_period(subscription_id)
                if last_period is None:
                    starts_at = now
                else:
                    starts_at = max(now, last_period["expires_at"])

        expires_at = starts_at + timedelta(days=period_days)
        subscription_period_id = await subscription_repo.create_period(
            subscription_id=subscription_id,
            starts_at=starts_at,
            expires_at=expires_at,
            is_paid=True,
            created_at=now,
        )
        return SubscriptionActivationResult(
            subscription_id=subscription_id,
            subscription_period_id=subscription_period_id,
            starts_at=starts_at,
            expires_at=expires_at,
            created_new_subscription=created_new_subscription,
        )
