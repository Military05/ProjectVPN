from __future__ import annotations

from collections.abc import Mapping

from shop_bot.application.commands._helpers import enqueue_job
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow


async def sync_expired_subscriptions(container: ServiceContainer) -> Mapping[str, int]:
    now = utcnow()
    async with container.uow() as uow:
        expired_subscription_ids = await uow.subscriptions.list_expired_active_subscription_ids(now)
        due_vpn_ids = await uow.vpn.list_active_configuration_ids_due_for_revoke(now)
        due_subscription_ids = await uow.subscriptions.list_due_subscriptions_for_provision(now)

        for subscription_id in expired_subscription_ids:
            await uow.subscriptions.end_subscription(subscription_id, ended_at=now, status="ended")
            await uow.payments.create_outbox_event(
                event_name="subscription_ended",
                aggregate_type="subscription",
                aggregate_id=subscription_id,
                payload={"subscription_id": subscription_id},
            )

    for vpn_configuration_id in due_vpn_ids:
        await enqueue_job(container, "revoke_vpn_configuration", vpn_configuration_id)
    for subscription_id in due_subscription_ids:
        await enqueue_job(container, "provision_subscription", subscription_id)
    await enqueue_job(container, "publish_outbox")

    return {
        "expired_subscriptions": len(expired_subscription_ids),
        "vpn_to_revoke": len(due_vpn_ids),
        "subscriptions_to_provision": len(due_subscription_ids),
    }
