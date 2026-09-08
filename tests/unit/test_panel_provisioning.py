from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from shop_bot.application.job_names import JobName
from shop_bot.application.use_cases.dispatch_panel_provision_task import DispatchPanelProvisionTask
from shop_bot.application.use_cases.provision_vpn import ProvisionVpn
from shop_bot.domain.entities.node import NodeTask
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
        self.outbox: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self.first_locked = asyncio.Event()
        self.release_first = asyncio.Event()
        self.block_first = False
        self.get_for_update: list[bool] = []


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
        return self.state.endpoint


class ProvisionPayments:
    def __init__(self, state: ProvisionState) -> None:
        self.state = state

    async def create_outbox_event(self, **payload: Any) -> int:
        self.state.outbox.append(payload)
        return len(self.state.outbox)


class ProvisionUow:
    def __init__(self, state: ProvisionState) -> None:
        self.state = state
        self.has_subscription_lock = False
        self.subscriptions = ProvisionSubscriptions(state, self)
        self.vpn = ProvisionVpnRepo(state)
        self.nodes = ProvisionNodes(state)
        self.servers = ProvisionServers(state)
        self.payments = ProvisionPayments(state)

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


class DispatchState:
    def __init__(self) -> None:
        payload = {"xui_inbound_id": 1, "client_uuid": str(CLIENT_UUID), "display_name": "vpn-1", "flow": None}
        self.task = PanelProvisionTask(None, 1, 1, PanelProvisionTaskStatus.PENDING, "panel-provision:vpn_configuration:1", payload=payload, max_attempts=5, next_retry_at=NOW)
        self.task.id = 11
        self.configuration = VpnConfiguration(1, 1, 7, CLIENT_UUID, "vpn-1", VpnConfigurationStatus.PROVISIONING)
        self.period: SubscriptionPeriod | None = SubscriptionPeriod(1, 1, NOW - timedelta(days=1), NOW + timedelta(days=1), True, NOW - timedelta(days=1))
        self.outbox: list[dict[str, Any]] = []
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


class DispatchPayments:
    def __init__(self, uow: "DispatchUow") -> None:
        self.uow = uow

    async def create_outbox_event(self, **payload: Any) -> int:
        self.uow.outbox_view.append(copy.deepcopy(payload))
        return len(self.uow.outbox_view)


class DispatchUow:
    def __init__(self, state: DispatchState, number: int) -> None:
        self.state = state
        self.number = number
        self.task_view = copy.deepcopy(state.task)
        self.config_view = copy.deepcopy(state.configuration)
        self.outbox_view = copy.deepcopy(state.outbox)
        self.vpn = DispatchVpnRepo(state, self)
        self.subscriptions = DispatchSubscriptions(state)
        self.payments = DispatchPayments(self)

    async def __aenter__(self) -> "DispatchUow":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc is not None:
            return None
        if self.number in self.state.fail_commit_numbers:
            raise RuntimeError("simulated local commit failure")
        self.state.task = self.task_view
        self.state.configuration = self.config_view
        self.state.outbox = self.outbox_view


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
async def test_panel_dispatch_success_is_fenced_and_activates_with_outbox() -> None:
    state = DispatchState()
    gateway = PanelGatewaySpy(state)
    result = await dispatcher(state, gateway).execute(panel_task_id=11)
    assert result["status"] == "succeeded"
    assert len(gateway.provisions) == 1
    assert state.task.status is PanelProvisionTaskStatus.SUCCEEDED
    assert state.configuration.status is VpnConfigurationStatus.ACTIVE
    assert state.outbox[0]["event_name"] == "vpn_configuration_activated"


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
    result = await dispatcher(state, gateway).execute(panel_task_id=11)
    assert result["status"] == "cancelled"
    assert len(gateway.provisions) == 1
    assert len(gateway.revokes) == 1
    assert state.configuration.status is VpnConfigurationStatus.FAILED
    assert state.configuration.desired_state is VpnDesiredState.REVOKED
    assert state.configuration.generation == 2
    assert state.task.status is PanelProvisionTaskStatus.CANCELLED


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
