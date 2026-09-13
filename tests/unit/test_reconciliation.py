from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from shop_bot.application.commands.sync_expired_subscriptions import (
    sync_expired_subscriptions,
)
from shop_bot.application.job_names import JobName
from shop_bot.application.use_cases.sync_subscriptions import ReconcileSubscriptions
from shop_bot.core.config import Settings
from shop_bot.domain.entities.subscription import Subscription, SubscriptionStatus
from shop_bot.domain.reconciliation import (
    ReconciliationAnomaly,
    ReconciliationAnomalyKind,
)


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
OWNER_TOKEN = UUID("11111111-1111-4111-8111-111111111111")


class Queue:
    def __init__(self, *, fail_for_entity_id: int | None = None) -> None:
        self.jobs: list[tuple[JobName, tuple[Any, ...]]] = []
        self.fail_for_entity_id = fail_for_entity_id

    async def enqueue(self, job_name: JobName, *args: Any) -> None:
        if args and args[0] == self.fail_for_entity_id:
            raise RuntimeError("redis unavailable")
        self.jobs.append((job_name, args))


class Maintenance:
    def __init__(
        self,
        anomalies: list[ReconciliationAnomaly],
        *,
        acquired: bool = True,
        renew_results: list[bool] | None = None,
    ) -> None:
        self.anomalies = anomalies
        self.acquired = acquired
        self.renew_results = list(renew_results or [])
        self.acquire_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.renew_calls: list[dict[str, Any]] = []
        self.release_calls: list[dict[str, Any]] = []

    async def acquire_lease(self, **kwargs: Any) -> bool:
        self.acquire_calls.append(kwargs)
        return self.acquired

    async def list_reconciliation_anomalies(self, **kwargs: Any) -> list[ReconciliationAnomaly]:
        self.list_calls.append(kwargs)
        cursor = (kwargs["after_kind_order"], kwargs["after_entity_id"])
        return [
            anomaly
            for anomaly in self.anomalies
            if (anomaly.kind_order, anomaly.entity_id) > cursor
        ][: kwargs["limit"]]

    async def renew_lease(self, **kwargs: Any) -> bool:
        self.renew_calls.append(kwargs)
        return self.renew_results.pop(0) if self.renew_results else True

    async def release_lease(self, **kwargs: Any) -> bool:
        self.release_calls.append(kwargs)
        return True


class Subscriptions:
    def __init__(self) -> None:
        self.entities = {
            1: Subscription(
                1,
                10,
                20,
                SubscriptionStatus.ACTIVE,
                NOW - timedelta(days=31),
            )
        }
        self.current_period_ids: set[int] = set()
        self.saved: list[int] = []

    async def get_entity(
        self,
        subscription_id: int,
        *,
        for_update: bool = False,
    ) -> Subscription | None:
        assert for_update is True
        return self.entities.get(subscription_id)

    async def get_current_period_entity(
        self,
        subscription_id: int,
        now: datetime,
    ) -> object | None:
        assert now == NOW
        return object() if subscription_id in self.current_period_ids else None

    async def save_entity(self, subscription: Subscription) -> None:
        assert subscription.id is not None
        self.saved.append(subscription.id)


class Payments:
    def __init__(self) -> None:
        self.outbox: list[dict[str, Any]] = []

    async def create_outbox_event(self, **kwargs: Any) -> int:
        self.outbox.append(kwargs)
        return len(self.outbox)


class Uow:
    def __init__(
        self,
        maintenance: Maintenance,
        subscriptions: Subscriptions,
        payments: Payments,
    ) -> None:
        self.maintenance = maintenance
        self.subscriptions = subscriptions
        self.payments = payments

    async def __aenter__(self) -> "Uow":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


def anomaly(
    order: int,
    kind: ReconciliationAnomalyKind,
    entity_id: int,
) -> ReconciliationAnomaly:
    return ReconciliationAnomaly(order, kind, entity_id)


def use_case(
    maintenance: Maintenance,
    queue: Queue,
    *,
    batch_size: int = 500,
    max_batches: int = 4,
) -> tuple[ReconcileSubscriptions, Subscriptions, Payments]:
    subscriptions = Subscriptions()
    payments = Payments()
    return (
        ReconcileSubscriptions(
            uow_factory=lambda: Uow(maintenance, subscriptions, payments),
            job_queue=queue,
            clock=lambda: NOW,
            batch_size=batch_size,
            max_batches_per_run=max_batches,
            lease_seconds=120,
            owner_token_factory=lambda: OWNER_TOKEN,
        ),
        subscriptions,
        payments,
    )


@pytest.mark.asyncio
async def test_consistent_large_dataset_boundary_enqueues_zero_jobs() -> None:
    maintenance = Maintenance([])
    queue = Queue()
    case, _, _ = use_case(maintenance, queue)

    result = await case.execute()

    assert result == {
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
    assert maintenance.list_calls[0]["limit"] == 500
    assert queue.jobs == []
    assert len(maintenance.acquire_calls) == len(maintenance.release_calls) == 1


@pytest.mark.asyncio
async def test_live_lease_returns_exact_already_running_result() -> None:
    maintenance = Maintenance([], acquired=False)
    case, _, _ = use_case(maintenance, Queue())

    assert await case.execute() == {"status": "already_running"}
    assert maintenance.list_calls == []
    assert maintenance.release_calls == []


@pytest.mark.asyncio
async def test_all_anomaly_classes_dispatch_only_their_surgical_action() -> None:
    maintenance = Maintenance(
        [
            anomaly(1, ReconciliationAnomalyKind.EXPIRE_SUBSCRIPTION, 1),
            anomaly(2, ReconciliationAnomalyKind.REVOKE_VPN, 10),
            anomaly(3, ReconciliationAnomalyKind.CLEANUP_VPN, 11),
            anomaly(4, ReconciliationAnomalyKind.PROVISION_SUBSCRIPTION, 2),
            anomaly(5, ReconciliationAnomalyKind.REPAIR_PROVISION, 3),
            anomaly(6, ReconciliationAnomalyKind.REPAIR_REVOKE, 12),
        ]
    )
    queue = Queue()
    case, subscriptions, payments = use_case(maintenance, queue)

    result = await case.execute()

    assert result["processed_anomalies"] == 6
    assert result["expired_subscriptions"] == 1
    assert result["vpn_to_revoke"] == 3
    assert result["subscriptions_to_provision"] == 2
    assert result["vpn_cleanup"] == 1
    assert result["provision_repairs"] == 1
    assert result["revoke_repairs"] == 1
    assert subscriptions.saved == [1]
    assert payments.outbox[0]["event_name"] == "subscription_ended"
    assert queue.jobs == [
        (JobName.REVOKE_VPN_CONFIGURATION, (10, "expiration")),
        (JobName.REVOKE_VPN_CONFIGURATION, (11, "replacement_cleanup")),
        (JobName.PROVISION_SUBSCRIPTION, (2,)),
        (JobName.PROVISION_SUBSCRIPTION, (3,)),
        (JobName.REVOKE_VPN_CONFIGURATION, (12, "force")),
        (JobName.PUBLISH_OUTBOX, ()),
    ]


@pytest.mark.asyncio
async def test_batches_use_keyset_cursor_renew_lease_and_stop_at_run_bound() -> None:
    maintenance = Maintenance(
        [
            anomaly(4, ReconciliationAnomalyKind.PROVISION_SUBSCRIPTION, entity_id)
            for entity_id in range(1, 6)
        ]
    )
    queue = Queue()
    case, _, _ = use_case(maintenance, queue, batch_size=2, max_batches=2)

    result = await case.execute()

    assert result["batches"] == 2
    assert result["processed_anomalies"] == 4
    assert [
        (call["after_kind_order"], call["after_entity_id"])
        for call in maintenance.list_calls
    ] == [(0, 0), (4, 2)]
    assert len(maintenance.renew_calls) == 2
    assert queue.jobs == [
        (JobName.PROVISION_SUBSCRIPTION, (1,)),
        (JobName.PROVISION_SUBSCRIPTION, (2,)),
        (JobName.PROVISION_SUBSCRIPTION, (3,)),
        (JobName.PROVISION_SUBSCRIPTION, (4,)),
    ]


@pytest.mark.asyncio
async def test_enqueue_failure_does_not_abort_batch_or_delete_anomaly() -> None:
    anomalies = [
        anomaly(4, ReconciliationAnomalyKind.PROVISION_SUBSCRIPTION, 2),
        anomaly(4, ReconciliationAnomalyKind.PROVISION_SUBSCRIPTION, 3),
    ]
    maintenance = Maintenance(anomalies)
    queue = Queue(fail_for_entity_id=2)
    case, _, _ = use_case(maintenance, queue)

    result = await case.execute()

    assert result["enqueue_failures"] == 1
    assert result["subscriptions_to_provision"] == 1
    assert maintenance.anomalies == anomalies
    assert queue.jobs == [(JobName.PROVISION_SUBSCRIPTION, (3,))]


@pytest.mark.asyncio
async def test_lost_lease_stops_after_current_bounded_batch_and_releases_fenced_token() -> None:
    maintenance = Maintenance(
        [
            anomaly(4, ReconciliationAnomalyKind.PROVISION_SUBSCRIPTION, entity_id)
            for entity_id in range(1, 5)
        ],
        renew_results=[False],
    )
    case, _, _ = use_case(maintenance, Queue(), batch_size=2, max_batches=4)

    result = await case.execute()

    assert result["status"] == "lease_lost"
    assert result["processed_anomalies"] == 2
    assert len(maintenance.list_calls) == 1
    assert maintenance.release_calls[0]["owner_token"] == OWNER_TOKEN


@pytest.mark.asyncio
async def test_old_command_name_delegates_to_new_application_service() -> None:
    expected = {"status": "completed", "processed_anomalies": 0}

    class Case:
        async def execute(self) -> dict[str, int | str]:
            return expected

    container = SimpleNamespace(
        applications=SimpleNamespace(reconcile_subscriptions=Case())
    )
    assert await sync_expired_subscriptions(container) is expected


@pytest.mark.parametrize("interval", [0, 45, 90, 3599, 3601])
def test_background_reconciliation_interval_rejects_inexact_cron_values(interval: int) -> None:
    with pytest.raises(ValueError, match="background_sync_interval_seconds"):
        Settings(background_sync_interval_seconds=interval)


@pytest.mark.parametrize("interval", [1, 5, 30, 60, 300, 900, 1800, 3600])
def test_background_reconciliation_interval_accepts_exact_cron_values(interval: int) -> None:
    assert Settings(background_sync_interval_seconds=interval).background_sync_interval_seconds == interval


def test_worker_cron_uses_configured_reconciliation_interval_and_new_entrypoint() -> None:
    from shop_bot.apps.worker.main import (
        WorkerSettings,
        reconciliation_cron_kwargs,
    )

    assert reconciliation_cron_kwargs(300) == {
        "minute": set(range(0, 60, 5))
    }
    assert reconciliation_cron_kwargs(30) == {
        "minute": set(range(60)),
        "second": {0, 30},
    }
    reconciliation_crons = [
        job
        for job in WorkerSettings.cron_jobs
        if getattr(job, "name", "") == "cron:reconcile_subscriptions_job"
    ]
    assert len(reconciliation_crons) == 1
    assert all(
        getattr(job, "name", "") != "cron:sync_expired_subscriptions_job"
        for job in WorkerSettings.cron_jobs
    )
