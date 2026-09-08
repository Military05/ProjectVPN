from __future__ import annotations

from shop_bot.application.job_names import JobName
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from shop_bot.application.ports import JobQueue, UnitOfWorkFactory
from shop_bot.application.revoke_reason import VpnRevokeReason


@dataclass(slots=True)
class SyncExpiredSubscriptions:
    uow_factory: UnitOfWorkFactory
    job_queue: JobQueue
    clock: Callable[[], datetime]

    async def execute(self) -> dict[str, int]:
        now = self.clock()
        async with self.uow_factory() as uow:
            expired_ids = await uow.subscriptions.list_expired_active_subscription_ids(now)
            due_vpn_ids = await uow.vpn.list_active_configuration_ids_due_for_revoke(now)
            due_subscription_ids = await uow.subscriptions.list_due_subscriptions_for_provision(now)
            expired_count = 0
            for subscription_id in expired_ids:
                subscription = await uow.subscriptions.get_entity(subscription_id, for_update=True)
                if subscription is None or str(subscription.status) != "active":
                    continue
                current_period = await uow.subscriptions.get_current_period_entity(subscription_id, now)
                if current_period is not None:
                    continue
                subscription.expire(now)
                await uow.subscriptions.save_entity(subscription)
                expired_count += 1
                await uow.payments.create_outbox_event(
                    event_name="subscription_ended",
                    aggregate_type="subscription",
                    aggregate_id=subscription_id,
                    payload={"subscription_id": subscription_id},
                )

        for vpn_configuration_id in due_vpn_ids:
            await self.job_queue.enqueue(
                JobName.REVOKE_VPN_CONFIGURATION,
                vpn_configuration_id,
                VpnRevokeReason.EXPIRATION.value,
            )
        for subscription_id in due_subscription_ids:
            await self.job_queue.enqueue(JobName.PROVISION_SUBSCRIPTION, subscription_id)
        await self.job_queue.enqueue(JobName.PUBLISH_OUTBOX)
        return {
            "expired_subscriptions": expired_count,
            "vpn_to_revoke": len(due_vpn_ids),
            "subscriptions_to_provision": len(due_subscription_ids),
        }
