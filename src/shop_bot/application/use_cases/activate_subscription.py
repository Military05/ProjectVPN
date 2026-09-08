from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from shop_bot.domain.repositories.interfaces import SubscriptionRepository
from shop_bot.domain.services.subscription_policy import SubscriptionActivation, SubscriptionPolicy


@dataclass(slots=True)
class ActivateSubscription:
    policy: SubscriptionPolicy

    async def execute(
        self,
        *,
        repository: SubscriptionRepository,
        user_id: int,
        tariff_id: int,
        period_days: int,
        now: datetime,
        payment_order_id: int | None = None,
    ) -> SubscriptionActivation:
        active = await repository.get_active_entity_for_user(user_id, for_update=True)
        last_period = None
        if active is not None and active.id is not None and active.tariff_id == tariff_id:
            last_period = await repository.get_last_period_entity(active.id, for_update=True)

        activation = self.policy.apply_paid_period(
            active_subscription=active,
            last_period=last_period,
            user_id=user_id,
            tariff_id=tariff_id,
            period_days=period_days,
            now=now,
        )
        if activation.replaced_subscription is not None:
            await repository.save_entity(activation.replaced_subscription)
        if activation.created_new_subscription:
            await repository.add_entity(activation.subscription)
        else:
            await repository.save_entity(activation.subscription)
        activation.period.subscription_id = activation.subscription.id
        activation.period.payment_order_id = payment_order_id
        await repository.add_period_entity(activation.period)
        return activation
