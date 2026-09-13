from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID, uuid4

from shop_bot.application.job_names import JobName
from shop_bot.application.ports import JobQueue, UnitOfWorkFactory
from shop_bot.application.revoke_reason import VpnRevokeReason
from shop_bot.domain.reconciliation import (
    ReconciliationAnomaly,
    ReconciliationAnomalyKind,
)


RECONCILIATION_LEASE_NAME = "reconcile_subscriptions"


@dataclass(slots=True)
class ReconcileSubscriptions:
    uow_factory: UnitOfWorkFactory
    job_queue: JobQueue
    clock: Callable[[], datetime]
    batch_size: int = 500
    max_batches_per_run: int = 4
    lease_seconds: int = 120
    owner_token_factory: Callable[[], UUID] = uuid4

    async def execute(self) -> dict[str, int | str]:
        owner_token = self.owner_token_factory()
        async with self.uow_factory() as uow:
            acquired = await uow.maintenance.acquire_lease(
                lease_name=RECONCILIATION_LEASE_NAME,
                owner_token=owner_token,
                now=self.clock(),
                lease_seconds=self.lease_seconds,
            )
        if not acquired:
            return {"status": "already_running"}

        result = self._empty_result()
        cursor = (0, 0)
        publish_outbox = False
        try:
            for _ in range(self.max_batches_per_run):
                batch_now = self.clock()
                async with self.uow_factory() as uow:
                    anomalies = await uow.maintenance.list_reconciliation_anomalies(
                        now=batch_now,
                        after_kind_order=cursor[0],
                        after_entity_id=cursor[1],
                        limit=self.batch_size,
                    )
                if not anomalies:
                    break

                for anomaly in anomalies:
                    result["processed_anomalies"] += 1
                    if anomaly.kind is ReconciliationAnomalyKind.EXPIRE_SUBSCRIPTION:
                        if await self._expire_subscription(anomaly.entity_id, batch_now):
                            result["expired_subscriptions"] += 1
                            publish_outbox = True
                        continue
                    try:
                        await self._enqueue_anomaly(anomaly)
                    except Exception:
                        result["enqueue_failures"] += 1
                        continue
                    self._record_enqueued(result, anomaly.kind)

                result["batches"] += 1
                last = anomalies[-1]
                cursor = (last.kind_order, last.entity_id)
                async with self.uow_factory() as uow:
                    renewed = await uow.maintenance.renew_lease(
                        lease_name=RECONCILIATION_LEASE_NAME,
                        owner_token=owner_token,
                        now=self.clock(),
                        lease_seconds=self.lease_seconds,
                    )
                if not renewed:
                    result["status"] = "lease_lost"
                    break
                if len(anomalies) < self.batch_size:
                    break

            if publish_outbox:
                try:
                    await self.job_queue.enqueue(JobName.PUBLISH_OUTBOX)
                except Exception:
                    result["enqueue_failures"] += 1
        finally:
            async with self.uow_factory() as uow:
                await uow.maintenance.release_lease(
                    lease_name=RECONCILIATION_LEASE_NAME,
                    owner_token=owner_token,
                )
        return result

    async def _expire_subscription(self, subscription_id: int, now: datetime) -> bool:
        async with self.uow_factory() as uow:
            subscription = await uow.subscriptions.get_entity(
                subscription_id,
                for_update=True,
            )
            if subscription is None or str(subscription.status) != "active":
                return False
            current_period = await uow.subscriptions.get_current_period_entity(
                subscription_id,
                now,
            )
            if current_period is not None:
                return False
            subscription.expire(now)
            await uow.subscriptions.save_entity(subscription)
            await uow.payments.create_outbox_event(
                event_name="subscription_ended",
                aggregate_type="subscription",
                aggregate_id=subscription_id,
                payload={"subscription_id": subscription_id},
            )
            return True

    async def _enqueue_anomaly(self, anomaly: ReconciliationAnomaly) -> None:
        if anomaly.kind is ReconciliationAnomalyKind.REVOKE_VPN:
            await self.job_queue.enqueue(
                JobName.REVOKE_VPN_CONFIGURATION,
                anomaly.entity_id,
                VpnRevokeReason.EXPIRATION.value,
            )
            return
        if anomaly.kind is ReconciliationAnomalyKind.CLEANUP_VPN:
            await self.job_queue.enqueue(
                JobName.REVOKE_VPN_CONFIGURATION,
                anomaly.entity_id,
                VpnRevokeReason.REPLACEMENT_CLEANUP.value,
            )
            return
        if anomaly.kind is ReconciliationAnomalyKind.REPAIR_REVOKE:
            await self.job_queue.enqueue(
                JobName.REVOKE_VPN_CONFIGURATION,
                anomaly.entity_id,
                VpnRevokeReason.FORCE.value,
            )
            return
        if anomaly.kind in {
            ReconciliationAnomalyKind.PROVISION_SUBSCRIPTION,
            ReconciliationAnomalyKind.REPAIR_PROVISION,
        }:
            await self.job_queue.enqueue(JobName.PROVISION_SUBSCRIPTION, anomaly.entity_id)
            return
        raise RuntimeError(f"Unsupported reconciliation anomaly: {anomaly.kind}")

    @staticmethod
    def _record_enqueued(
        result: dict[str, int | str],
        kind: ReconciliationAnomalyKind,
    ) -> None:
        if kind in {
            ReconciliationAnomalyKind.REVOKE_VPN,
            ReconciliationAnomalyKind.CLEANUP_VPN,
            ReconciliationAnomalyKind.REPAIR_REVOKE,
        }:
            result["vpn_to_revoke"] += 1
        if kind in {
            ReconciliationAnomalyKind.PROVISION_SUBSCRIPTION,
            ReconciliationAnomalyKind.REPAIR_PROVISION,
        }:
            result["subscriptions_to_provision"] += 1
        if kind is ReconciliationAnomalyKind.CLEANUP_VPN:
            result["vpn_cleanup"] += 1
        elif kind is ReconciliationAnomalyKind.REPAIR_PROVISION:
            result["provision_repairs"] += 1
        elif kind is ReconciliationAnomalyKind.REPAIR_REVOKE:
            result["revoke_repairs"] += 1

    @staticmethod
    def _empty_result() -> dict[str, int | str]:
        return {
            "status": "completed",
            "batches": 0,
            "processed_anomalies": 0,
            "expired_subscriptions": 0,
            "vpn_to_revoke": 0,
            "subscriptions_to_provision": 0,
            "vpn_cleanup": 0,
            "provision_repairs": 0,
            "revoke_repairs": 0,
            "enqueue_failures": 0,
        }
