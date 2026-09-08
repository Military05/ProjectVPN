from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from shop_bot.application.use_cases.dispatch_node_task import DispatchNodeTask
from shop_bot.domain.entities.node import NodeTask, NodeTaskOperation, NodeTaskStatus
from shop_bot.domain.entities.subscription import Subscription, SubscriptionPeriod, SubscriptionStatus
from shop_bot.domain.entities.vpn import VpnConfiguration, VpnConfigurationStatus, VpnDesiredState


NOW = datetime(2026, 8, 19, 13, 0, tzinfo=UTC)
CLIENT_UUID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class State:
    def __init__(self, config: VpnConfiguration) -> None:
        self.config = config
        self.tasks: list[NodeTask] = []
        self.outbox: list[dict[str, Any]] = []
        self.period: SubscriptionPeriod | None = SubscriptionPeriod(
            1, 1, NOW - timedelta(days=1), NOW + timedelta(days=1), True, NOW - timedelta(days=1)
        )
        self.lock_order: list[str] = []


class VpnRepo:
    def __init__(self, state: State) -> None:
        self.state = state

    async def get_entity(self, config_id: int, *, for_update: bool = False) -> VpnConfiguration | None:
        if for_update:
            self.state.lock_order.append("vpn")
        return self.state.config if config_id == 7 else None

    async def save_entity(self, config: VpnConfiguration) -> None:
        self.state.config = config


class Nodes:
    def __init__(self, state: State) -> None:
        self.state = state

    async def add_task_entity(self, task: NodeTask) -> NodeTask:
        task.id = len(self.state.tasks) + 100
        self.state.tasks.append(task)
        return task


class Subscriptions:
    def __init__(self, state: State) -> None:
        self.state = state

    async def get_entity(self, subscription_id: int, *, for_update: bool = False) -> Subscription | None:
        if for_update:
            self.state.lock_order.append("subscription")
        return Subscription(1, 10, 20, SubscriptionStatus.ACTIVE, NOW - timedelta(days=10)) if subscription_id == 1 else None

    async def get_current_period_entity(self, subscription_id: int, now: datetime) -> SubscriptionPeriod | None:
        del subscription_id, now
        return self.state.period


class Payments:
    def __init__(self, state: State) -> None:
        self.state = state

    async def create_outbox_event(self, **payload: Any) -> int:
        self.state.outbox.append(payload)
        return len(self.state.outbox)


class Uow:
    def __init__(self, state: State) -> None:
        self.vpn = VpnRepo(state)
        self.nodes = Nodes(state)
        self.subscriptions = Subscriptions(state)
        self.payments = Payments(state)


class Gateway:
    pass


class Queue:
    async def enqueue(self, job_name: Any, *args: Any) -> None:
        del job_name, args


def dispatcher(state: State) -> DispatchNodeTask:
    return DispatchNodeTask(
        uow_factory=lambda: None,  # direct finalizer tests provide UOW explicitly
        node_gateway=Gateway(),  # type: ignore[arg-type]
        job_queue=Queue(),
        retry_base_seconds=15,
        lease_seconds=60,
        clock=lambda: NOW,
    )


def provision_task(*, generation: int = 1, key: str = "provision-old") -> NodeTask:
    task_uuid = uuid4()
    return NodeTask(
        id=1,
        task_uuid=task_uuid,
        node_id=5,
        operation=NodeTaskOperation.PROVISION_CLIENT,
        status=NodeTaskStatus.SUCCEEDED,
        idempotency_key=key,
        payload={
            "task_id": str(task_uuid),
            "idempotency_key": key,
            "client_uuid": str(CLIENT_UUID),
            "inbound_id": "main-vless",
            "display_name": "vpn-1",
            "metadata": {"vpn_configuration_id": 7, "vpn_generation": generation},
        },
        vpn_configuration_id=7,
        subscription_id=1,
        vpn_generation=generation,
        remote_client_ref="remote-old",
        max_attempts=5,
    )


def config(*, status: VpnConfigurationStatus, desired: VpnDesiredState, generation: int) -> VpnConfiguration:
    return VpnConfiguration(
        7,
        1,
        9,
        CLIENT_UUID,
        "vpn-1",
        status,
        remote_client_ref="remote-current",
        desired_state=desired,
        generation=generation,
    )


@pytest.mark.asyncio
async def test_late_provision_after_succeeded_revoke_creates_fresh_compensation_key() -> None:
    state = State(config(status=VpnConfigurationStatus.REVOKED, desired=VpnDesiredState.REVOKED, generation=2))
    uow = Uow(state)
    old_provision = provision_task(generation=1, key="provision-generation-1")
    prior_revoke_key = "revoke:vpn_configuration:7:generation:2:task:already-completed"

    result = await dispatcher(state)._complete_provision(
        uow,
        task=old_provision,
        remote_status="provisioned",
        completed_at=NOW,
    )

    assert state.config.status is VpnConfigurationStatus.REVOKED
    assert state.config.generation == 2
    assert state.outbox == []
    assert result.follow_up_task_id == 100
    assert len(state.tasks) == 1
    compensation = state.tasks[0]
    assert compensation.operation is NodeTaskOperation.REVOKE_CLIENT
    assert compensation.vpn_generation == 2
    assert compensation.idempotency_key not in {old_provision.idempotency_key, prior_revoke_key}
    assert compensation.payload["task_id"] == str(compensation.task_uuid)
    assert compensation.payload["idempotency_key"] == compensation.idempotency_key


@pytest.mark.asyncio
async def test_paid_period_disappears_during_current_provision_cancels_and_cleans_up() -> None:
    state = State(config(status=VpnConfigurationStatus.PROVISIONING, desired=VpnDesiredState.ACTIVE, generation=1))
    state.period = None
    uow = Uow(state)

    result = await dispatcher(state)._complete_provision(
        uow,
        task=provision_task(generation=1),
        remote_status="provisioned",
        completed_at=NOW,
    )

    assert state.lock_order == ["subscription", "vpn"]
    assert state.config.status is VpnConfigurationStatus.FAILED
    assert state.config.desired_state is VpnDesiredState.REVOKED
    assert state.config.generation == 2
    assert result.follow_up_task_id == 100
    assert state.tasks[0].vpn_generation == 2


@pytest.mark.asyncio
async def test_stale_provision_failure_does_not_mutate_new_generation() -> None:
    state = State(config(status=VpnConfigurationStatus.REVOKING, desired=VpnDesiredState.REVOKED, generation=2))
    task = provision_task(generation=1)

    await DispatchNodeTask._mark_configuration_failure(Uow(state), task)

    assert state.config.status is VpnConfigurationStatus.REVOKING
    assert state.config.generation == 2


@pytest.mark.asyncio
async def test_stale_revoke_failure_does_not_mutate_new_generation() -> None:
    state = State(config(status=VpnConfigurationStatus.REVOKING, desired=VpnDesiredState.REVOKED, generation=3))
    task_uuid = uuid4()
    task = NodeTask(
        id=2,
        task_uuid=task_uuid,
        node_id=5,
        operation=NodeTaskOperation.REVOKE_CLIENT,
        status=NodeTaskStatus.FAILED,
        idempotency_key="revoke-old",
        payload={"task_id": str(task_uuid), "idempotency_key": "revoke-old", "client_uuid": str(CLIENT_UUID), "inbound_id": "main-vless"},
        vpn_configuration_id=7,
        subscription_id=1,
        vpn_generation=2,
    )

    await DispatchNodeTask._mark_configuration_failure(Uow(state), task)

    assert state.config.status is VpnConfigurationStatus.REVOKING
    assert state.config.generation == 3


@pytest.mark.asyncio
async def test_revoke_success_only_transitions_current_generation() -> None:
    current = State(config(status=VpnConfigurationStatus.REVOKING, desired=VpnDesiredState.REVOKED, generation=2))
    task_uuid = uuid4()
    task = NodeTask(
        id=3,
        task_uuid=task_uuid,
        node_id=5,
        operation=NodeTaskOperation.REVOKE_CLIENT,
        status=NodeTaskStatus.SUCCEEDED,
        idempotency_key="revoke-current",
        payload={"task_id": str(task_uuid), "idempotency_key": "revoke-current", "client_uuid": str(CLIENT_UUID), "inbound_id": "main-vless"},
        vpn_configuration_id=7,
        subscription_id=1,
        vpn_generation=2,
    )
    result = await dispatcher(current)._complete_revoke(Uow(current), task=task, remote_status="revoked", completed_at=NOW)
    assert current.config.status is VpnConfigurationStatus.REVOKED
    assert result.publish_outbox is True
    assert [e["event_name"] for e in current.outbox] == ["vpn_configuration_revoked"]

    stale = State(config(status=VpnConfigurationStatus.REVOKING, desired=VpnDesiredState.REVOKED, generation=3))
    stale_result = await dispatcher(stale)._complete_revoke(Uow(stale), task=task, remote_status="revoked", completed_at=NOW)
    assert stale.config.status is VpnConfigurationStatus.REVOKING
    assert stale.config.generation == 3
    assert stale_result.publish_outbox is False
    assert stale.outbox == []
