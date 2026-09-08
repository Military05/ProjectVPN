from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from shop_bot.application.job_names import JobName
from shop_bot.application.use_cases.dispatch_panel_revoke_task import DispatchPanelRevokeTask
from shop_bot.domain.entities.panel_task import PanelRevokeTask, PanelRevokeTaskStatus
from shop_bot.domain.entities.vpn import VpnConfiguration, VpnConfigurationStatus, VpnDesiredState


NOW = datetime(2026, 8, 19, 14, 0, tzinfo=UTC)
CLIENT_UUID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


class State:
    def __init__(self, *, generation: int = 2, max_attempts: int = 3) -> None:
        self.config = VpnConfiguration(
            7,
            1,
            9,
            CLIENT_UUID,
            "vpn-1",
            VpnConfigurationStatus.REVOKING,
            desired_state=VpnDesiredState.REVOKED,
            generation=generation,
        )
        self.task = PanelRevokeTask(
            id=11,
            task_uuid=uuid4(),
            vpn_configuration_id=7,
            subscription_id=1,
            vpn_generation=generation,
            status=PanelRevokeTaskStatus.PENDING,
            idempotency_key=f"panel-revoke-7-{generation}",
            payload={"xui_inbound_id": 1, "client_uuid": str(CLIENT_UUID)},
            max_attempts=max_attempts,
            next_retry_at=NOW,
        )
        self.outbox: list[dict[str, Any]] = []
        self.in_tx = False


class VpnRepo:
    def __init__(self, state: State) -> None:
        self.state = state

    async def get_panel_revoke_task_entity(self, task_id: int, *, for_update: bool = False) -> PanelRevokeTask | None:
        del for_update
        return self.state.task if task_id == 11 else None

    async def save_panel_revoke_task_entity(self, task: PanelRevokeTask) -> None:
        self.state.task = task

    async def get_entity(self, config_id: int, *, for_update: bool = False) -> VpnConfiguration | None:
        del for_update
        return self.state.config if config_id == 7 else None

    async def save_entity(self, config: VpnConfiguration) -> None:
        self.state.config = config

    async def list_due_panel_revoke_task_ids(self, now: datetime, limit: int = 100) -> list[int]:
        del limit
        return [11] if self.state.task.can_dispatch(now) else []

    async def list_stale_panel_revoke_task_entities(self, now: datetime, limit: int = 100) -> list[PanelRevokeTask]:
        del limit
        return [self.state.task] if self.state.task.lease_is_expired(now) else []


class Payments:
    def __init__(self, state: State) -> None:
        self.state = state

    async def create_outbox_event(self, **payload: Any) -> int:
        self.state.outbox.append(payload)
        return len(self.state.outbox)


class Uow:
    def __init__(self, state: State) -> None:
        self.state = state
        self.vpn = VpnRepo(state)
        self.payments = Payments(state)

    async def __aenter__(self) -> "Uow":
        assert not self.state.in_tx
        self.state.in_tx = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.state.in_tx = False


class Queue:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, tuple[Any, ...]]] = []

    async def enqueue(self, job_name: Any, *args: Any) -> None:
        self.jobs.append((job_name, args))


class Gateway:
    def __init__(self, state: State, *, fail: bool = False) -> None:
        self.state = state
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def revoke(self, payload: dict[str, Any]) -> None:
        # The remote side effect must never run while the PostgreSQL UOW is open.
        assert self.state.in_tx is False
        self.calls.append(dict(payload))
        if self.fail:
            raise RuntimeError("remote revoke failed")


def dispatcher(state: State, gateway: Gateway, queue: Queue | None = None) -> DispatchPanelRevokeTask:
    return DispatchPanelRevokeTask(
        uow_factory=lambda: Uow(state),
        panel_gateway=gateway,
        job_queue=queue or Queue(),
        retry_base_seconds=15,
        lease_seconds=30,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_panel_revoke_success_finalizes_current_generation_once() -> None:
    state = State()
    queue = Queue()
    gateway = Gateway(state)

    result = await dispatcher(state, gateway, queue).execute(panel_revoke_task_id=11)

    assert result["status"] == "succeeded"
    assert len(gateway.calls) == 1
    assert state.task.status is PanelRevokeTaskStatus.SUCCEEDED
    assert state.config.status is VpnConfigurationStatus.REVOKED
    assert [event["event_name"] for event in state.outbox] == ["vpn_configuration_revoked"]
    assert queue.jobs == [(JobName.PUBLISH_OUTBOX, ())]


@pytest.mark.asyncio
async def test_stale_panel_revoke_success_does_not_overwrite_newer_generation() -> None:
    state = State(generation=3)
    state.task.vpn_generation = 2
    gateway = Gateway(state)

    result = await dispatcher(state, gateway).execute(panel_revoke_task_id=11)

    assert result["status"] == "succeeded"
    assert state.task.status is PanelRevokeTaskStatus.SUCCEEDED
    assert state.config.status is VpnConfigurationStatus.REVOKING
    assert state.config.generation == 3
    assert state.outbox == []


@pytest.mark.asyncio
async def test_panel_revoke_max_failure_only_marks_current_generation_revoke_failed() -> None:
    state = State(max_attempts=1)
    gateway = Gateway(state, fail=True)

    result = await dispatcher(state, gateway).execute(panel_revoke_task_id=11)

    assert result["status"] == "failed"
    assert state.task.status is PanelRevokeTaskStatus.FAILED
    assert state.config.status is VpnConfigurationStatus.REVOKE_FAILED

    stale = State(generation=3, max_attempts=1)
    stale.task.vpn_generation = 2
    stale_result = await dispatcher(stale, Gateway(stale, fail=True)).execute(panel_revoke_task_id=11)
    assert stale_result["status"] == "failed"
    assert stale.config.status is VpnConfigurationStatus.REVOKING
    assert stale.config.generation == 3


@pytest.mark.asyncio
async def test_panel_revoke_crash_window_recovers_same_task_and_same_idempotency_key() -> None:
    state = State()
    original_key = state.task.idempotency_key
    original_uuid = state.task.task_uuid
    token = uuid4()
    state.task.next_retry_at = NOW - timedelta(minutes=3)
    state.task.start_attempt(
        now=NOW - timedelta(minutes=2),
        lease_expires_at=NOW - timedelta(minutes=1),
        lease_token=token,
    )
    queue = Queue()
    gateway = Gateway(state)
    case = dispatcher(state, gateway, queue)

    recovered = await case.recover_stale()
    assert recovered == {"recovered": 1, "queued": 1}
    assert state.task.status is PanelRevokeTaskStatus.PENDING
    assert state.task.idempotency_key == original_key
    assert state.task.task_uuid == original_uuid

    result = await case.execute(panel_revoke_task_id=11)
    assert result["status"] == "succeeded"
    assert state.config.status is VpnConfigurationStatus.REVOKED
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_due_panel_revoke_dispatch_uses_new_job_name() -> None:
    state = State()
    queue = Queue()
    result = await dispatcher(state, Gateway(state), queue).dispatch_due()
    assert result == {"queued": 1}
    assert queue.jobs == [(JobName.DISPATCH_PANEL_REVOKE_TASK, (11,))]
