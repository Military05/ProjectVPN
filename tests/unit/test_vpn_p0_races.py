from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
import importlib
import sys
from types import ModuleType
from uuid import UUID

import pytest

from shop_bot.application.job_names import JobName
from shop_bot.application.revoke_reason import VpnRevokeReason
from shop_bot.application.use_cases.admin_operations import AdminOperations
from shop_bot.application.use_cases.revoke_vpn import RevokeVpn
from shop_bot.application.use_cases.sync_subscriptions import SyncExpiredSubscriptions
from shop_bot.domain.entities.node import NodeTask, NodeTaskOperation, NodeTaskStatus
from shop_bot.domain.entities.panel_task import PanelRevokeTask, PanelRevokeTaskStatus
from shop_bot.domain.entities.subscription import Subscription, SubscriptionPeriod, SubscriptionStatus
from shop_bot.domain.entities.vpn import VpnConfiguration, VpnConfigurationStatus, VpnDesiredState


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
CLIENT_UUID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class Queue:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, tuple[Any, ...]]] = []

    async def enqueue(self, job_name: Any, *args: Any) -> None:
        self.jobs.append((job_name, args))


class State:
    def __init__(self, *, node_id: int | None = 5) -> None:
        self.subscription = Subscription(1, 10, 20, SubscriptionStatus.ACTIVE, NOW - timedelta(days=30))
        self.period: SubscriptionPeriod | None = None
        self.config = VpnConfiguration(
            7,
            1,
            9,
            CLIENT_UUID,
            "vpn-1",
            VpnConfigurationStatus.ACTIVE,
        )
        self.node_id = node_id
        self.node_tasks: list[NodeTask] = []
        self.panel_tasks: list[PanelRevokeTask] = []
        self.outbox: list[dict[str, Any]] = []
        self.saved_subscriptions = 0
        self.saved_configs = 0
        self.lock_order: list[str] = []
        self.expired_candidates = [1]
        self.due_vpn_ids = [7]
        self.due_provision_ids: list[int] = []


class Subscriptions:
    def __init__(self, state: State) -> None:
        self.state = state

    async def get_entity(self, subscription_id: int, *, for_update: bool = False) -> Subscription | None:
        if for_update:
            self.state.lock_order.append("subscription")
        return self.state.subscription if subscription_id == 1 else None

    async def get_current_period_entity(self, subscription_id: int, now: datetime) -> SubscriptionPeriod | None:
        del subscription_id, now
        return self.state.period

    async def save_entity(self, subscription: Subscription) -> None:
        self.state.subscription = subscription
        self.state.saved_subscriptions += 1

    async def list_expired_active_subscription_ids(self, now: datetime) -> list[int]:
        del now
        return list(self.state.expired_candidates)

    async def list_due_subscriptions_for_provision(self, now: datetime) -> list[int]:
        del now
        return list(self.state.due_provision_ids)


class VpnRepo:
    def __init__(self, state: State) -> None:
        self.state = state

    async def get_configuration_with_endpoint(self, vpn_configuration_id: int) -> dict[str, Any] | None:
        if vpn_configuration_id != 7:
            return None
        c = self.state.config
        return {
            "vpn_configuration_id": 7,
            "subscription_id": 1,
            "server_endpoint_id": 9,
            "client_uuid": c.client_uuid,
            "display_name": c.display_name,
            "status": str(c.status),
            "remote_client_ref": c.remote_client_ref,
            "created_at": c.created_at,
            "revoked_at": c.revoked_at,
            "node_id": self.state.node_id,
            "local_inbound_id": "main-vless",
        }

    async def get_entity(self, vpn_configuration_id: int, *, for_update: bool = False) -> VpnConfiguration | None:
        if for_update:
            self.state.lock_order.append("vpn")
        return self.state.config if vpn_configuration_id == 7 else None

    async def save_entity(self, configuration: VpnConfiguration) -> None:
        self.state.config = configuration
        self.state.saved_configs += 1

    async def list_active_configuration_ids_due_for_revoke(self, now: datetime) -> list[int]:
        del now
        return list(self.state.due_vpn_ids)

    async def get_panel_revoke_task_for_generation(self, vpn_configuration_id: int, vpn_generation: int) -> PanelRevokeTask | None:
        matches = [
            t for t in self.state.panel_tasks
            if t.vpn_configuration_id == vpn_configuration_id and t.vpn_generation == vpn_generation
        ]
        return matches[-1] if matches else None

    async def add_panel_revoke_task_entity(self, task: PanelRevokeTask) -> PanelRevokeTask:
        task.id = len(self.state.panel_tasks) + 1
        self.state.panel_tasks.append(task)
        return task


class Nodes:
    def __init__(self, state: State) -> None:
        self.state = state

    async def get_vpn_task_for_generation(self, vpn_configuration_id: int, operation: str, vpn_generation: int) -> NodeTask | None:
        for task in reversed(self.state.node_tasks):
            if (
                task.vpn_configuration_id == vpn_configuration_id
                and str(task.operation) == operation
                and task.vpn_generation == vpn_generation
                and task.status in {NodeTaskStatus.PENDING, NodeTaskStatus.IN_PROGRESS}
            ):
                return task
        return None

    async def add_task_entity(self, task: NodeTask) -> NodeTask:
        task.id = len(self.state.node_tasks) + 1
        self.state.node_tasks.append(task)
        return task


class Payments:
    def __init__(self, state: State) -> None:
        self.state = state

    async def create_outbox_event(self, **payload: Any) -> int:
        self.state.outbox.append(payload)
        return len(self.state.outbox)


class Uow:
    def __init__(self, state: State) -> None:
        self.subscriptions = Subscriptions(state)
        self.vpn = VpnRepo(state)
        self.nodes = Nodes(state)
        self.payments = Payments(state)

    async def __aenter__(self) -> "Uow":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


def revoke_case(state: State, queue: Queue) -> RevokeVpn:
    return RevokeVpn(
        uow_factory=lambda: Uow(state),
        job_queue=queue,
        node_inbound_id="main-vless",
        node_task_max_attempts=5,
        panel_task_max_attempts=5,
        xui_inbound_id=1,
        clock=lambda: NOW,
    )


def current_period() -> SubscriptionPeriod:
    return SubscriptionPeriod(3, 1, NOW - timedelta(hours=1), NOW + timedelta(days=30), True, NOW)


@pytest.mark.asyncio
async def test_subscription_renewed_during_expiration_sync_is_not_ended() -> None:
    state = State()
    # Candidate was selected as expired, but authoritative post-lock read sees renewal.
    state.period = current_period()
    queue = Queue()
    result = await SyncExpiredSubscriptions(lambda: Uow(state), queue, lambda: NOW).execute()

    assert result["expired_subscriptions"] == 0
    assert state.subscription.status is SubscriptionStatus.ACTIVE
    assert state.saved_subscriptions == 0
    assert state.outbox == []
    assert (JobName.REVOKE_VPN_CONFIGURATION, (7, "expiration")) in queue.jobs


@pytest.mark.asyncio
async def test_actual_expired_count_tracks_real_transitions_not_candidates() -> None:
    state = State()
    state.expired_candidates = [1, 999]
    state.due_vpn_ids = []
    queue = Queue()
    result = await SyncExpiredSubscriptions(lambda: Uow(state), queue, lambda: NOW).execute()

    assert result["expired_subscriptions"] == 1
    assert state.subscription.status is SubscriptionStatus.ENDED
    assert state.saved_subscriptions == 1
    assert [event["event_name"] for event in state.outbox] == ["subscription_ended"]


@pytest.mark.asyncio
async def test_stale_expiration_revoke_does_not_revoke_renewed_subscription() -> None:
    state = State()
    state.period = current_period()
    queue = Queue()

    result = await revoke_case(state, queue).execute(vpn_configuration_id=7, reason="expiration")

    assert result["status"] == "skipped_current_access"
    assert state.lock_order == ["subscription"]
    assert state.config.status is VpnConfigurationStatus.ACTIVE
    assert state.config.desired_state is VpnDesiredState.ACTIVE
    assert state.config.generation == 1
    assert state.node_tasks == []
    assert queue.jobs == []


@pytest.mark.asyncio
async def test_expiration_revoke_without_current_period_creates_generation_fenced_intent() -> None:
    state = State()
    queue = Queue()

    result = await revoke_case(state, queue).execute(vpn_configuration_id=7, reason=VpnRevokeReason.EXPIRATION)

    assert result["status"] == "queued"
    assert state.lock_order == ["subscription", "vpn"]
    assert state.config.status is VpnConfigurationStatus.REVOKING
    assert state.config.desired_state is VpnDesiredState.REVOKED
    assert state.config.generation == 2
    assert len(state.node_tasks) == 1
    task = state.node_tasks[0]
    assert task.operation is NodeTaskOperation.REVOKE_CLIENT
    assert task.vpn_generation == 2
    assert task.payload["task_id"] == str(task.task_uuid)
    assert task.idempotency_key == task.payload["idempotency_key"]
    assert queue.jobs == [(JobName.DISPATCH_NODE_TASK, (1,))]


@pytest.mark.asyncio
async def test_force_revoke_ignores_paid_period_but_keeps_vpn_lock_and_generation_rules() -> None:
    state = State()
    state.period = current_period()
    queue = Queue()

    await revoke_case(state, queue).execute(vpn_configuration_id=7, reason="force")

    assert state.lock_order == ["vpn"]
    assert state.config.status is VpnConfigurationStatus.REVOKING
    assert state.config.generation == 2
    assert len(state.node_tasks) == 1


@pytest.mark.asyncio
async def test_repeated_normal_revoke_reuses_current_generation_task() -> None:
    state = State()
    queue = Queue()
    case = revoke_case(state, queue)

    first = await case.execute(vpn_configuration_id=7, reason="force")
    original_key = state.node_tasks[0].idempotency_key
    second = await case.execute(vpn_configuration_id=7, reason="force")

    assert first["node_task_id"] == second["node_task_id"] == 1
    assert len(state.node_tasks) == 1
    assert state.node_tasks[0].idempotency_key == original_key
    assert state.config.generation == 2


@pytest.mark.asyncio
async def test_panel_revoke_is_persisted_before_dispatch_and_repeated_call_reuses_it() -> None:
    state = State(node_id=None)
    queue = Queue()
    case = revoke_case(state, queue)

    first = await case.execute(vpn_configuration_id=7, reason="force")
    second = await case.execute(vpn_configuration_id=7, reason="force")

    assert first["panel_task_id"] == second["panel_task_id"] == 1
    assert len(state.panel_tasks) == 1
    assert state.panel_tasks[0].vpn_generation == 2
    assert state.panel_tasks[0].payload == {"xui_inbound_id": "main-vless", "client_uuid": str(CLIENT_UUID)}
    assert queue.jobs == [(JobName.DISPATCH_PANEL_REVOKE_TASK, (1,))]


@pytest.mark.asyncio
async def test_force_retry_after_failed_panel_cleanup_starts_fresh_generation_and_operation() -> None:
    state = State(node_id=None)
    state.config.status = VpnConfigurationStatus.FAILED
    state.config.desired_state = VpnDesiredState.REVOKED
    state.config.generation = 2
    failed_task = PanelRevokeTask(
        id=1,
        task_uuid=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        vpn_configuration_id=7,
        subscription_id=1,
        vpn_generation=2,
        status=PanelRevokeTaskStatus.FAILED,
        idempotency_key="panel-revoke:old-failed-operation",
        payload={"xui_inbound_id": 1, "client_uuid": str(CLIENT_UUID)},
        attempts=5,
        max_attempts=5,
        completed_at=NOW,
    )
    state.panel_tasks.append(failed_task)
    queue = Queue()

    result = await revoke_case(state, queue).execute(vpn_configuration_id=7, reason="force")

    assert result["status"] == "queued"
    assert result["panel_task_id"] == 2
    assert state.config.status is VpnConfigurationStatus.FAILED
    assert state.config.desired_state is VpnDesiredState.REVOKED
    assert state.config.generation == 3
    assert len(state.panel_tasks) == 2
    fresh_task = state.panel_tasks[1]
    assert fresh_task.vpn_generation == 3
    assert fresh_task.status is PanelRevokeTaskStatus.PENDING
    assert fresh_task.idempotency_key != failed_task.idempotency_key
    assert fresh_task.task_uuid != failed_task.task_uuid
    assert queue.jobs == [(JobName.DISPATCH_PANEL_REVOKE_TASK, (2,))]


@pytest.mark.asyncio
async def test_admin_queue_revoke_marks_operation_as_force() -> None:
    queue = Queue()
    operations = AdminOperations(
        uow_factory=lambda: Uow(State()),
        job_queue=queue,
        reconcile_subscriptions=None,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    await operations.queue_revoke(7)
    assert queue.jobs == [(JobName.REVOKE_VPN_CONFIGURATION, (7, "force"))]


@pytest.mark.asyncio
async def test_legacy_one_argument_worker_revoke_defaults_to_expiration(monkeypatch: pytest.MonkeyPatch) -> None:
    if "arq" not in sys.modules:
        arq_module = ModuleType("arq")
        connections = ModuleType("arq.connections")
        worker = ModuleType("arq.worker")
        arq_module.cron = lambda coroutine, **kwargs: coroutine  # type: ignore[attr-defined]

        class RedisSettingsStub:
            @classmethod
            def from_dsn(cls, dsn: str) -> "RedisSettingsStub":
                del dsn
                return cls()

        connections.RedisSettings = RedisSettingsStub  # type: ignore[attr-defined]
        worker.func = lambda coroutine, **kwargs: coroutine  # type: ignore[attr-defined]
        worker.run_worker = lambda settings: None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "arq", arq_module)
        monkeypatch.setitem(sys.modules, "arq.connections", connections)
        monkeypatch.setitem(sys.modules, "arq.worker", worker)
    sys.modules.pop("shop_bot.apps.worker.main", None)
    worker_main = importlib.import_module("shop_bot.apps.worker.main")

    captured: dict[str, Any] = {}

    async def fake_command(container: object, *, vpn_configuration_id: int, reason: str) -> dict[str, Any]:
        captured.update(container=container, vpn_configuration_id=vpn_configuration_id, reason=reason)
        return {"status": "ok"}

    monkeypatch.setattr(worker_main, "revoke_vpn_configuration", fake_command)
    container = object()
    result = await worker_main.revoke_vpn_configuration_job({"container": container}, 7)
    assert result == {"status": "ok"}
    assert captured == {"container": container, "vpn_configuration_id": 7, "reason": "expiration"}
