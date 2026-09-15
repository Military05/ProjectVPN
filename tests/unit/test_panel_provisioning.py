from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from shop_bot.application.job_names import JobName
from shop_bot.application.revoke_reason import VpnRevokeReason
from shop_bot.application.use_cases.dispatch_panel_provision_task import DispatchPanelProvisionTask
from shop_bot.application.use_cases.provision_vpn import ProvisionVpn
from shop_bot.domain.entities.node import NodeTask, NodeTaskOperation, NodeTaskStatus
from shop_bot.domain.entities.panel_task import PanelProvisionTask, PanelProvisionTaskStatus
from shop_bot.domain.entities.subscription import Subscription, SubscriptionPeriod, SubscriptionStatus
from shop_bot.domain.entities.vpn import VpnConfiguration, VpnConfigurationStatus, VpnDesiredState
from shop_bot.domain.services.vpn_provisioning import VpnProvisioningService
from shop_bot.domain.vpn.builder import VlessUriBuilder


NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
CLIENT_UUID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class Queue:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, tuple[Any, ...]]] = []

    async def enqueue(self, job_name: Any, *args: Any) -> None:
        self.jobs.append((job_name, args))


class ProvisionState:
    def __init__(self, *, node_id: int | None) -> None:
        self.subscription = Subscription(1, 10, 20, SubscriptionStatus.ACTIVE, NOW - timedelta(days=1))
        self.period = SubscriptionPeriod(1, 1, NOW - timedelta(days=1), NOW + timedelta(days=29), True, NOW - timedelta(days=1))
        self.endpoint = {
            "server_endpoint_id": 7,
            "host": "vpn.example.test",
            "server_name": "test",
            "port": 443,
            "node_id": node_id,
            "local_inbound_id": "main-vless",
            "security": None,
            "sni": None,
            "fingerprint": None,
            "public_key": None,
            "short_id": None,
            "transport_type": None,
            "flow": None,
            "encryption": None,
        }
        self.configs: list[VpnConfiguration] = []
        self.node_tasks: dict[str, NodeTask] = {}
        self.panel_tasks: dict[int, PanelProvisionTask] = {}
        self.audit_events: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self.first_locked = asyncio.Event()
        self.release_first = asyncio.Event()
        self.block_first = False
        self.get_for_update: list[bool] = []
        self.endpoint_selection_calls = 0
        self.node_health_status = "online"
        self.node_is_enabled = True
        self.node_last_checked_at = NOW
        self.node_active_clients = 0
        self.node_max_clients = 500
        self.node_selection_weight = 100
        self.node_local_reserved_clients = 0
        self.locked_local_reserved_clients: int | None = None
        self.node_lock_calls: list[tuple[int, int]] = []


class ProvisionSubscriptions:
    def __init__(self, state: ProvisionState, uow: "ProvisionUow") -> None:
        self.state = state
        self.uow = uow

    async def get_entity(self, subscription_id: int, *, for_update: bool = False) -> Subscription | None:
        assert subscription_id == 1
        self.state.get_for_update.append(for_update)
        if for_update:
            await self.state.lock.acquire()
            self.uow.has_subscription_lock = True
            if self.state.block_first and not self.state.first_locked.is_set():
                self.state.first_locked.set()
                await self.state.release_first.wait()
        return self.state.subscription

    async def get_current_period_entity(self, subscription_id: int, now: datetime) -> SubscriptionPeriod | None:
        del subscription_id, now
        return self.state.period


class ProvisionVpnRepo:
    def __init__(self, state: ProvisionState) -> None:
        self.state = state

    async def get_active_entity_for_subscription(self, subscription_id: int) -> VpnConfiguration | None:
        return next((c for c in self.state.configs if c.subscription_id == subscription_id and c.status is VpnConfigurationStatus.ACTIVE), None)

    async def get_latest_entity_for_subscription(self, subscription_id: int) -> VpnConfiguration | None:
        matches = [c for c in self.state.configs if c.subscription_id == subscription_id]
        return matches[-1] if matches else None

    async def add_entity(self, configuration: VpnConfiguration) -> VpnConfiguration:
        configuration.id = len(self.state.configs) + 1
        self.state.configs.append(configuration)
        return configuration

    async def save_entity(self, configuration: VpnConfiguration) -> None:
        assert configuration.id is not None
        self.state.configs[configuration.id - 1] = configuration

    async def get_configuration_with_endpoint(self, vpn_configuration_id: int) -> dict[str, Any] | None:
        config = self.state.configs[vpn_configuration_id - 1]
        return {**self.state.endpoint, "vpn_configuration_id": config.id, "subscription_id": config.subscription_id, "client_uuid": config.client_uuid, "display_name": config.display_name, "status": str(config.status), "remote_client_ref": config.remote_client_ref, "created_at": config.created_at, "revoked_at": config.revoked_at}

    async def get_panel_task_for_configuration(self, vpn_configuration_id: int) -> PanelProvisionTask | None:
        return self.state.panel_tasks.get(vpn_configuration_id)

    async def add_panel_task_entity(self, task: PanelProvisionTask) -> PanelProvisionTask:
        existing = self.state.panel_tasks.get(task.vpn_configuration_id)
        if existing is not None:
            return existing
        task.id = len(self.state.panel_tasks) + 1
        self.state.panel_tasks[task.vpn_configuration_id] = task
        return task


class ProvisionNodes:
    def __init__(self, state: ProvisionState) -> None:
        self.state = state

    async def get_vpn_task_for_generation(self, vpn_configuration_id: int, operation: str, vpn_generation: int) -> NodeTask | None:
        for task in self.state.node_tasks.values():
            if (
                task.vpn_configuration_id == vpn_configuration_id
                and str(task.operation) == operation
                and task.vpn_generation == vpn_generation
            ):
                return task
        return None

    async def add_task_entity(self, task: NodeTask) -> NodeTask:
        existing = self.state.node_tasks.get(task.idempotency_key)
        if existing is not None:
            return existing
        task.id = len(self.state.node_tasks) + 1
        self.state.node_tasks[task.idempotency_key] = task
        return task


class ProvisionServers:
    def __init__(self, state: ProvisionState) -> None:
        self.state = state

    async def get_first_enabled_endpoint(self) -> dict[str, Any]:
        self.state.endpoint_selection_calls += 1
        return self.state.endpoint

    def _candidate(self, *, locked: bool) -> dict[str, Any]:
        local_reserved = self.state.node_local_reserved_clients
        if locked and self.state.locked_local_reserved_clients is not None:
            local_reserved = self.state.locked_local_reserved_clients
        return {
            **self.state.endpoint,
            "health_status": self.state.node_health_status,
            "node_is_enabled": self.state.node_is_enabled,
            "last_checked_at": self.state.node_last_checked_at,
            "active_clients": self.state.node_active_clients,
            "max_clients": self.state.node_max_clients,
            "selection_weight": self.state.node_selection_weight,
            "local_reserved_clients": local_reserved,
        }

    async def list_node_selection_candidates(self) -> list[dict[str, Any]]:
        if self.state.endpoint["node_id"] is None:
            return []
        return [self._candidate(locked=False)]

    async def lock_and_revalidate_candidate(
        self,
        node_id: int,
        endpoint_id: int,
    ) -> dict[str, Any] | None:
        self.state.endpoint_selection_calls += 1
        self.state.node_lock_calls.append((node_id, endpoint_id))
        if (
            node_id != self.state.endpoint["node_id"]
            or endpoint_id != self.state.endpoint["server_endpoint_id"]
        ):
            return None
        return self._candidate(locked=True)


class ProvisionAudit:
    def __init__(self, state: ProvisionState) -> None:
        self.state = state

    async def append(self, **payload: Any) -> int:
        self.state.audit_events.append(payload)
        return len(self.state.audit_events)


class ProvisionUow:
    def __init__(self, state: ProvisionState) -> None:
        self.state = state
        self.has_subscription_lock = False
        self.subscriptions = ProvisionSubscriptions(state, self)
        self.vpn = ProvisionVpnRepo(state)
        self.nodes = ProvisionNodes(state)
        self.servers = ProvisionServers(state)
        self.audit = ProvisionAudit(state)

    async def __aenter__(self) -> "ProvisionUow":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.has_subscription_lock and self.state.lock.locked():
            self.state.lock.release()


def provision_use_case(state: ProvisionState, queue: Queue) -> ProvisionVpn:
    return ProvisionVpn(
        uow_factory=lambda: ProvisionUow(state),
        provisioning_service=VpnProvisioningService(VlessUriBuilder(), uuid_factory=lambda: CLIENT_UUID),
        job_queue=queue,
        display_name_prefix="vpn",
        node_inbound_id="main-vless",
        node_task_max_attempts=5,
        panel_task_max_attempts=5,
        xui_inbound_id=1,
        clock=lambda: NOW,
    )


def seed_configuration(
    state: ProvisionState,
    *,
    status: VpnConfigurationStatus = VpnConfigurationStatus.PROVISIONING,
    desired_state: VpnDesiredState = VpnDesiredState.ACTIVE,
    generation: int = 1,
) -> VpnConfiguration:
    configuration = VpnConfiguration(
        id=1,
        subscription_id=1,
        server_endpoint_id=7,
        client_uuid=CLIENT_UUID,
        display_name="vpn-1",
        status=status,
        desired_state=desired_state,
        generation=generation,
    )
    state.configs.append(configuration)
    return configuration


def seed_provision_task(
    state: ProvisionState,
    status: str,
) -> NodeTask | PanelProvisionTask:
    if state.endpoint["node_id"] is not None:
        task = NodeTask(
            id=41,
            task_uuid=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            node_id=int(state.endpoint["node_id"]),
            operation=NodeTaskOperation.PROVISION_CLIENT,
            status=NodeTaskStatus(status),
            idempotency_key="provision-existing-generation-1",
            payload={
                "task_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "idempotency_key": "provision-existing-generation-1",
                "client_uuid": str(CLIENT_UUID),
                "inbound_id": "main-vless",
            },
            vpn_configuration_id=1,
            subscription_id=1,
            vpn_generation=1,
            remote_client_ref="remote-client-1" if status == "succeeded" else None,
        )
        state.node_tasks[task.idempotency_key] = task
        return task
    task = PanelProvisionTask(
        id=42,
        vpn_configuration_id=1,
        subscription_id=1,
        status=PanelProvisionTaskStatus(status),
        idempotency_key="panel-provision:vpn_configuration:1",
        payload={"xui_inbound_id": 1, "client_uuid": str(CLIENT_UUID)},
        vpn_generation=1,
    )
    state.panel_tasks[1] = task
    return task


@pytest.mark.asyncio
@pytest.mark.parametrize("node_id", [5, None])
async def test_concurrent_provisioning_serializes_subscription_and_creates_one_config_task(node_id: int | None) -> None:
    state = ProvisionState(node_id=node_id)
    state.block_first = True
    queue = Queue()
    use_case = provision_use_case(state, queue)

    first = asyncio.create_task(use_case.execute(subscription_id=1))
    await state.first_locked.wait()
    second = asyncio.create_task(use_case.execute(subscription_id=1))
    await asyncio.sleep(0)
    state.release_first.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert state.get_for_update == [True, True]
    assert len(state.configs) == 1
    assert state.configs[0].status is VpnConfigurationStatus.PROVISIONING
    assert first_result["vpn_configuration_id"] == second_result["vpn_configuration_id"] == 1
    if node_id is not None:
        assert len(state.node_tasks) == 1
        assert len(state.panel_tasks) == 0
        assert all(job[0] == JobName.DISPATCH_NODE_TASK for job in queue.jobs)
    else:
        assert len(state.panel_tasks) == 1
        assert len(state.node_tasks) == 0
        task = next(iter(state.panel_tasks.values()))
        assert task.payload["client_uuid"] == str(CLIENT_UUID)
        assert all(job[0] == JobName.DISPATCH_PANEL_PROVISION_TASK for job in queue.jobs)


@pytest.mark.asyncio
async def test_node_capacity_is_revalidated_under_lock_without_side_effects() -> None:
    state = ProvisionState(node_id=5)
    state.node_max_clients = 1
    state.node_local_reserved_clients = 0
    state.locked_local_reserved_clients = 1
    queue = Queue()

    result = await provision_use_case(state, queue).execute(subscription_id=1)

    assert result == {"status": "waiting_for_node_capacity"}
    assert state.node_lock_calls == [(5, 7)]
    assert state.configs == []
    assert state.node_tasks == {}
    assert state.panel_tasks == {}
    assert state.audit_events == []
    assert queue.jobs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("node_id", [5, None])
@pytest.mark.parametrize("task_status", ["pending", "in_progress"])
async def test_existing_nonterminal_provision_operation_is_reused_without_new_identity(
    node_id: int | None,
    task_status: str,
) -> None:
    state = ProvisionState(node_id=node_id)
    seed_configuration(state)
    task = seed_provision_task(state, task_status)
    queue = Queue()

    result = await provision_use_case(state, queue).execute(subscription_id=1)

    assert result["vpn_configuration_id"] == 1
    assert len(state.configs) == 1
    assert state.endpoint_selection_calls == 0
    assert len(state.node_tasks) + len(state.panel_tasks) == 1
    if node_id is not None:
        assert result["node_task_id"] == task.id
        expected_job = (JobName.DISPATCH_NODE_TASK, (task.id,))
    else:
        assert result["panel_task_id"] == task.id
        expected_job = (JobName.DISPATCH_PANEL_PROVISION_TASK, (task.id,))
    assert queue.jobs == ([expected_job] if task_status == "pending" else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("node_id", [5, None])
async def test_succeeded_provision_operation_recovers_locally_without_remote_replay(
    node_id: int | None,
) -> None:
    state = ProvisionState(node_id=node_id)
    configuration = seed_configuration(state)
    seed_provision_task(state, "succeeded")
    queue = Queue()
    use_case = provision_use_case(state, queue)

    first = await use_case.execute(subscription_id=1)
    second = await use_case.execute(subscription_id=1)

    assert first == second == {"status": "active", "vpn_configuration_id": 1}
    assert configuration.status is VpnConfigurationStatus.ACTIVE
    assert configuration.remote_client_ref == (
        "remote-client-1" if node_id is not None else None
    )
    assert state.endpoint_selection_calls == 0
    assert len(state.node_tasks) + len(state.panel_tasks) == 1
    assert [event["event_name"] for event in state.audit_events] == [
        "vpn_configuration_activated"
    ]
    assert queue.jobs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("node_id", [5, None])
@pytest.mark.parametrize("task_status", ["failed", "cancelled"])
async def test_terminal_failed_provision_operation_queues_cleanup_without_new_identity(
    node_id: int | None,
    task_status: str,
) -> None:
    state = ProvisionState(node_id=node_id)
    configuration = seed_configuration(state)
    original_task = seed_provision_task(state, task_status)
    queue = Queue()

    result = await provision_use_case(state, queue).execute(subscription_id=1)

    assert result == {"status": "cleanup_required", "vpn_configuration_id": 1}
    assert configuration.status is VpnConfigurationStatus.PROVISIONING
    assert configuration.desired_state is VpnDesiredState.ACTIVE
    assert configuration.generation == 1
    assert state.endpoint_selection_calls == 0
    assert len(state.configs) == 1
    assert len(state.node_tasks) + len(state.panel_tasks) == 1
    stored_task = (
        next(iter(state.node_tasks.values()))
        if node_id is not None
        else next(iter(state.panel_tasks.values()))
    )
    assert stored_task is original_task
    assert queue.jobs == [
        (
            JobName.REVOKE_VPN_CONFIGURATION,
            (1, VpnRevokeReason.REPLACEMENT_CLEANUP.value),
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("node_id", [5, None])
async def test_failed_configuration_must_cleanup_before_endpoint_reselection(
    node_id: int | None,
) -> None:
    state = ProvisionState(node_id=node_id)
    configuration = seed_configuration(
        state,
        status=VpnConfigurationStatus.FAILED,
    )
    seed_provision_task(state, "failed")
    queue = Queue()
    use_case = provision_use_case(state, queue)

    first = await use_case.execute(subscription_id=1)
    second = await use_case.execute(subscription_id=1)

    assert first == second == {"status": "cleanup_required", "vpn_configuration_id": 1}
    assert configuration.status is VpnConfigurationStatus.FAILED
    assert configuration.desired_state is VpnDesiredState.ACTIVE
    assert configuration.generation == 1
    assert state.endpoint_selection_calls == 0
    assert len(state.configs) == 1
    assert len(state.node_tasks) + len(state.panel_tasks) == 1
    assert queue.jobs == [
        (
            JobName.REVOKE_VPN_CONFIGURATION,
            (1, VpnRevokeReason.REPLACEMENT_CLEANUP.value),
        ),
        (
            JobName.REVOKE_VPN_CONFIGURATION,
            (1, VpnRevokeReason.REPLACEMENT_CLEANUP.value),
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("node_id", [5, None])
async def test_replacement_is_created_only_after_cleanup_reaches_revoked(
    node_id: int | None,
) -> None:
    state = ProvisionState(node_id=node_id)
    old_configuration = seed_configuration(
        state,
        status=VpnConfigurationStatus.FAILED,
    )
    seed_provision_task(state, "failed")
    queue = Queue()
    use_case = provision_use_case(state, queue)

    blocked = await use_case.execute(subscription_id=1)
    assert blocked == {"status": "cleanup_required", "vpn_configuration_id": 1}
    assert len(state.configs) == 1
    assert state.endpoint_selection_calls == 0

    old_configuration.request_cleanup_from_failed()
    old_configuration.revoke(NOW)
    replacement = await use_case.execute(subscription_id=1)

    assert replacement["status"] == "queued"
    assert replacement["vpn_configuration_id"] == 2
    assert old_configuration.status is VpnConfigurationStatus.REVOKED
    assert state.configs[1].status is VpnConfigurationStatus.PROVISIONING
    assert state.endpoint_selection_calls == 1
    assert len(state.node_tasks) + len(state.panel_tasks) == 2


class DispatchState:
    def __init__(self) -> None:
        payload = {"xui_inbound_id": 1, "client_uuid": str(CLIENT_UUID), "display_name": "vpn-1", "flow": None}
        self.task = PanelProvisionTask(None, 1, 1, PanelProvisionTaskStatus.PENDING, "panel-provision:vpn_configuration:1", payload=payload, max_attempts=5, next_retry_at=NOW)
        self.task.id = 11
        self.configuration = VpnConfiguration(1, 1, 7, CLIENT_UUID, "vpn-1", VpnConfigurationStatus.PROVISIONING)
        self.period: SubscriptionPeriod | None = SubscriptionPeriod(1, 1, NOW - timedelta(days=1), NOW + timedelta(days=1), True, NOW - timedelta(days=1))
        self.audit_events: list[dict[str, Any]] = []
        self.uow_count = 0
        self.fail_commit_numbers: set[int] = set()


class DispatchVpnRepo:
    def __init__(self, state: DispatchState, uow: "DispatchUow") -> None:
        self.state = state
        self.uow = uow

    async def get_panel_task_entity(self, task_id: int, *, for_update: bool = False) -> PanelProvisionTask | None:
        del for_update
        return copy.deepcopy(self.uow.task_view) if task_id == self.state.task.id else None

    async def save_panel_task_entity(self, task: PanelProvisionTask) -> None:
        self.uow.task_view = copy.deepcopy(task)

    async def get_entity(self, config_id: int, *, for_update: bool = False) -> VpnConfiguration | None:
        del for_update
        return copy.deepcopy(self.uow.config_view) if config_id == 1 else None

    async def save_entity(self, config: VpnConfiguration) -> None:
        self.uow.config_view = copy.deepcopy(config)

    async def list_due_panel_task_ids(self, now: datetime, limit: int = 100) -> list[int]:
        del limit
        task = self.state.task
        return [11] if task.can_dispatch(now) else []

    async def list_stale_panel_task_entities(self, now: datetime, limit: int = 100) -> list[PanelProvisionTask]:
        del limit
        return [copy.deepcopy(self.state.task)] if self.state.task.lease_is_expired(now) else []


class DispatchSubscriptions:
    def __init__(self, state: DispatchState) -> None:
        self.state = state

    async def get_entity(self, subscription_id: int, *, for_update: bool = False) -> Subscription | None:
        del for_update
        if subscription_id != 1:
            return None
        return Subscription(1, 10, 20, SubscriptionStatus.ACTIVE, NOW - timedelta(days=1))

    async def get_current_period_entity(self, subscription_id: int, now: datetime) -> SubscriptionPeriod | None:
        del subscription_id, now
        return copy.deepcopy(self.state.period)


class DispatchAudit:
    def __init__(self, uow: "DispatchUow") -> None:
        self.uow = uow

    async def append(self, **payload: Any) -> int:
        self.uow.audit_view.append(copy.deepcopy(payload))
        return len(self.uow.audit_view)


class DispatchUow:
    def __init__(self, state: DispatchState, number: int) -> None:
        self.state = state
        self.number = number
        self.task_view = copy.deepcopy(state.task)
        self.config_view = copy.deepcopy(state.configuration)
        self.audit_view = copy.deepcopy(state.audit_events)
        self.vpn = DispatchVpnRepo(state, self)
        self.subscriptions = DispatchSubscriptions(state)
        self.audit = DispatchAudit(self)

    async def __aenter__(self) -> "DispatchUow":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc is not None:
            return None
        if self.number in self.state.fail_commit_numbers:
            raise RuntimeError("simulated local commit failure")
        self.state.task = self.task_view
        self.state.configuration = self.config_view
        self.state.audit_events = self.audit_view


class DispatchUowFactory:
    def __init__(self, state: DispatchState) -> None:
        self.state = state

    def __call__(self) -> DispatchUow:
        self.state.uow_count += 1
        return DispatchUow(self.state, self.state.uow_count)


class PanelGatewaySpy:
    def __init__(self, state: DispatchState) -> None:
        self.state = state
        self.provisions: list[dict[str, Any]] = []
        self.revokes: list[dict[str, Any]] = []
        self.fail_revoke = False
        self.expire_during_provision = False

    async def provision(self, payload: dict[str, Any]) -> None:
        self.provisions.append(copy.deepcopy(payload))
        if self.expire_during_provision:
            self.state.period = None

    async def revoke(self, payload: dict[str, Any]) -> None:
        self.revokes.append(copy.deepcopy(payload))
        if self.fail_revoke:
            raise RuntimeError("revoke failed")


def dispatcher(state: DispatchState, gateway: PanelGatewaySpy, queue: Queue | None = None) -> DispatchPanelProvisionTask:
    return DispatchPanelProvisionTask(
        uow_factory=DispatchUowFactory(state),
        panel_gateway=gateway,
        job_queue=queue or Queue(),
        retry_base_seconds=15,
        lease_seconds=30,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_panel_dispatch_success_is_fenced_and_appends_audit_event() -> None:
    state = DispatchState()
    gateway = PanelGatewaySpy(state)
    result = await dispatcher(state, gateway).execute(panel_task_id=11)
    assert result["status"] == "succeeded"
    assert len(gateway.provisions) == 1
    assert state.task.status is PanelProvisionTaskStatus.SUCCEEDED
    assert state.configuration.status is VpnConfigurationStatus.ACTIVE
    assert state.audit_events[0]["event_name"] == "vpn_configuration_activated"


@pytest.mark.asyncio
async def test_remote_success_local_commit_failure_triggers_same_client_compensation() -> None:
    state = DispatchState()
    state.fail_commit_numbers.add(2)
    gateway = PanelGatewaySpy(state)
    with pytest.raises(RuntimeError, match="simulated local commit failure"):
        await dispatcher(state, gateway).execute(panel_task_id=11)
    assert len(gateway.provisions) == 1
    assert len(gateway.revokes) == 1
    assert gateway.revokes[0]["client_uuid"] == gateway.provisions[0]["client_uuid"] == str(CLIENT_UUID)
    assert state.task.status is not PanelProvisionTaskStatus.SUCCEEDED
    assert state.configuration.status is VpnConfigurationStatus.PROVISIONING
    assert state.audit_events == []


@pytest.mark.asyncio
async def test_failed_compensation_blocks_blind_second_provision_until_cleanup_succeeds() -> None:
    state = DispatchState()
    state.fail_commit_numbers.add(2)
    gateway = PanelGatewaySpy(state)
    gateway.fail_revoke = True
    with pytest.raises(RuntimeError):
        await dispatcher(state, gateway).execute(panel_task_id=11)
    assert state.task.compensation_required is True
    assert len(gateway.provisions) == 1

    gateway.fail_revoke = False
    result = await dispatcher(state, gateway).execute(panel_task_id=11)
    assert result["status"] in {"pending", "failed"}
    assert len(gateway.provisions) == 1
    assert len(gateway.revokes) == 2
    assert state.task.compensation_required is False


@pytest.mark.asyncio
async def test_stale_lease_recovery_requires_compensation_before_new_provision_and_fences_old_owner() -> None:
    state = DispatchState()
    gateway = PanelGatewaySpy(state)
    use_case = dispatcher(state, gateway)
    old = await use_case._prepare(11)
    assert not isinstance(old, dict)
    state.task.lease_expires_at = NOW - timedelta(seconds=1)
    await use_case.recover_stale()
    assert state.task.compensation_required is True
    assert state.task.status is PanelProvisionTaskStatus.PENDING

    outcome = await use_case._finalize_provision_success(old)
    assert outcome == "lease_lost"
    assert state.configuration.status is VpnConfigurationStatus.PROVISIONING

    await use_case.execute(panel_task_id=11)
    assert len(gateway.revokes) == 1
    assert len(gateway.provisions) == 0


@pytest.mark.asyncio
async def test_access_expiring_during_remote_provision_is_compensated_and_never_activated() -> None:
    state = DispatchState()
    gateway = PanelGatewaySpy(state)
    gateway.expire_during_provision = True
    queue = Queue()
    result = await dispatcher(state, gateway, queue).execute(panel_task_id=11)
    assert result["status"] == "cancelled"
    assert len(gateway.provisions) == 1
    assert len(gateway.revokes) == 1
    assert state.configuration.status is VpnConfigurationStatus.REVOKED
    assert state.configuration.desired_state is VpnDesiredState.REVOKED
    assert state.configuration.generation == 2
    assert state.task.status is PanelProvisionTaskStatus.CANCELLED
    assert [event["event_name"] for event in state.audit_events] == [
        "vpn_configuration_revoked"
    ]
    assert queue.jobs == []


@pytest.mark.asyncio
async def test_stale_panel_provision_success_compensates_and_never_reprovisions_revoked_generation() -> None:
    state = DispatchState()
    state.configuration.status = VpnConfigurationStatus.REVOKED
    state.configuration.desired_state = VpnDesiredState.REVOKED
    state.configuration.generation = 2
    state.task.vpn_generation = 1
    gateway = PanelGatewaySpy(state)

    result = await dispatcher(state, gateway).execute(panel_task_id=11)

    assert result["status"] == "cancelled"
    assert len(gateway.provisions) == 1
    assert len(gateway.revokes) == 1
    assert state.configuration.status is VpnConfigurationStatus.REVOKED
    assert state.configuration.generation == 2
    assert state.task.status is PanelProvisionTaskStatus.CANCELLED
    assert state.task.compensation_required is False
