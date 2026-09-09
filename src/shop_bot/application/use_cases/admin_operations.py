from __future__ import annotations

from shop_bot.application.job_names import JobName
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from shop_bot.application.ports import JobQueue, UnitOfWorkFactory
from shop_bot.application.revoke_reason import VpnRevokeReason
from shop_bot.application.use_cases.sync_subscriptions import SyncExpiredSubscriptions
from shop_bot.application.use_cases.activate_subscription import ActivateSubscription
from shop_bot.core.exceptions import NotFoundError


@dataclass(slots=True)
class AdminOperations:
    uow_factory: UnitOfWorkFactory
    job_queue: JobQueue
    reconcile_subscriptions: SyncExpiredSubscriptions
    clock: Callable[[], datetime]
    activate_subscription: ActivateSubscription | None = None

    async def list_tariffs(self) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            rows = await uow.admin.list_tariffs(include_disabled=True)
        return [dict(row) for row in rows]

    async def create_tariff(self, **payload: Any) -> dict[str, Any]:
        async with self.uow_factory() as uow:
            row = await uow.admin.create_tariff(**payload)
        return dict(row)

    async def list_servers(self) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            return [dict(row) for row in await uow.servers.list_servers()]

    async def create_server(self, **payload: Any) -> dict[str, Any]:
        async with self.uow_factory() as uow:
            return dict(await uow.servers.create_server(**payload))

    async def list_server_endpoints(self) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            return [dict(row) for row in await uow.servers.list_server_endpoints()]

    async def create_server_endpoint(self, **payload: Any) -> dict[str, Any]:
        async with self.uow_factory() as uow:
            return dict(await uow.servers.create_server_endpoint(**payload))

    async def list_subscriptions(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            rows = await uow.subscriptions.list_subscriptions(limit=limit, offset=offset)
            return [dict(row) for row in rows]

    async def list_vpn_configurations(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            rows = await uow.vpn.list_configurations(limit=limit, offset=offset)
            return [dict(row) for row in rows]

    async def list_payment_orders(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            rows = await uow.payments.list_orders(limit=limit, offset=offset)
            return [dict(row) for row in rows]

    async def mark_payment_order_paid(self, payment_order_id: int) -> dict[str, int | str]:
        now = self.clock()
        subscription_id: int | None = None
        async with self.uow_factory() as uow:
            order = await uow.payments.get_order_entity(payment_order_id, for_update=True)
            if order is None:
                raise NotFoundError("Order not found")
            if not order.mark_paid(now):
                return {"payment_order_id": payment_order_id, "status": "already_paid"}
            order.metadata = {**order.metadata, "manual_settlement": {"settled_at": now.isoformat(), "source": "admin"}}
            await uow.payments.save_order_entity(order)
            if self.activate_subscription is None:
                raise RuntimeError("ActivateSubscription is not configured")
            activation = await self.activate_subscription.execute(
                repository=uow.subscriptions,
                user_id=order.user_id,
                tariff_id=order.tariff_id,
                period_days=order.requested_period_days,
                now=now,
                payment_order_id=int(order.id),
            )
            if activation.subscription.id is None or activation.period.id is None:
                raise RuntimeError("Subscription activation was not persisted")
            subscription_id = int(activation.subscription.id)
            await uow.payments.create_outbox_event(
                event_name="subscription_activated",
                aggregate_type="subscription",
                aggregate_id=subscription_id,
                payload={
                    "subscription_id": subscription_id,
                    "subscription_period_id": int(activation.period.id),
                    "starts_at": activation.period.starts_at.isoformat(),
                    "expires_at": activation.period.expires_at.isoformat(),
                },
            )
            await uow.payments.create_outbox_event(
                event_name="payment_manually_settled",
                aggregate_type="payment_order",
                aggregate_id=int(order.id),
                payload={"payment_order_id": int(order.id), "subscription_id": subscription_id},
            )
        await self.job_queue.enqueue(JobName.PROVISION_SUBSCRIPTION, subscription_id)
        await self.job_queue.enqueue(JobName.PUBLISH_OUTBOX)
        return {"payment_order_id": payment_order_id, "subscription_id": subscription_id, "status": "paid"}

    async def queue_provisioning(self, subscription_id: int) -> dict[str, int | str]:
        await self.job_queue.enqueue(JobName.PROVISION_SUBSCRIPTION, subscription_id)
        return {"subscription_id": subscription_id, "status": "queued"}

    async def queue_revoke(self, vpn_configuration_id: int) -> dict[str, int | str]:
        await self.job_queue.enqueue(
            JobName.REVOKE_VPN_CONFIGURATION,
            vpn_configuration_id,
            VpnRevokeReason.FORCE.value,
        )
        return {"vpn_configuration_id": vpn_configuration_id, "status": "queued"}

    async def reconcile(self) -> dict[str, int]:
        return await self.reconcile_subscriptions.execute()

    async def get_payment_with_attempt(self, payment_order_id: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
        async with self.uow_factory() as uow:
            order = await uow.payments.get_order(payment_order_id)
            if order is None:
                raise NotFoundError("Order not found")
            attempt = await uow.payments.get_latest_attempt(payment_order_id)
        return dict(order), dict(attempt) if attempt is not None else None
