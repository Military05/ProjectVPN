from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from shop_bot.application.use_cases.dispatch_node_task import DispatchNodeTask, PreparedDispatch
from shop_bot.domain.entities.node import NodeTask, NodeTaskOperation, NodeTaskStatus
from shop_bot.domain.entities.vpn import VpnConfigurationStatus, VpnDesiredState


START = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)


def pending_task(*, attempts: int = 0, max_attempts: int = 5) -> NodeTask:
    return NodeTask(
        id=10,
        task_uuid=uuid4(),
        node_id=20,
        operation=NodeTaskOperation.PROVISION_CLIENT,
        status=NodeTaskStatus.PENDING,
        idempotency_key="provision:vpn:10",
        payload={"task_id": "10", "client_uuid": "c", "inbound_id": "i", "idempotency_key": "provision:vpn:10"},
        attempts=attempts,
        max_attempts=max_attempts,
        next_retry_at=START,
    )


def test_claim_sets_lease_and_terminal_retry_transitions_clear_it() -> None:
    task = pending_task()
    token = uuid4()
    expiry = START + timedelta(seconds=60)
    assert task.start_attempt(START, lease_expires_at=expiry, lease_token=token) == 1
    assert task.status is NodeTaskStatus.IN_PROGRESS
    assert task.claimed_at == START
    assert task.lease_expires_at == expiry
    assert task.lease_token == token
    assert task.owns_active_lease(token, START + timedelta(seconds=1), 1)
    assert not task.lease_is_expired(START + timedelta(seconds=59))
    assert task.lease_is_expired(expiry)

    task.retry(error="offline", response=None, now=expiry, base_delay_seconds=10)
    assert task.status is NodeTaskStatus.PENDING
    assert task.claimed_at is None and task.lease_expires_at is None and task.lease_token is None


def test_expired_lease_at_max_attempts_becomes_failed_and_clears_lease() -> None:
    task = pending_task(attempts=1, max_attempts=2)
    token = uuid4()
    expiry = START + timedelta(seconds=60)
    task.start_attempt(START, lease_expires_at=expiry, lease_token=token)
    task.recover_expired_lease(now=expiry, error="lease expired", base_delay_seconds=10)
    assert task.status is NodeTaskStatus.FAILED
    assert task.attempts == 2
    assert task.lease_token is None
    assert task.completed_at == expiry


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeConfiguration:
    def __init__(self) -> None:
        self.provision_failed = False
        self.revoke_failed = False
        self.generation = 1
        self.desired_state = VpnDesiredState.ACTIVE
        self.status = VpnConfigurationStatus.PROVISIONING

    def fail_provisioning(self) -> None:
        self.provision_failed = True
        self.status = VpnConfigurationStatus.FAILED

    def fail_revoke(self) -> None:
        self.revoke_failed = True
        self.status = VpnConfigurationStatus.REVOKE_FAILED


class FakeVpnRepository:
    def __init__(self) -> None:
        self.configuration = FakeConfiguration()
        self.saved = 0

    async def get_entity(self, vpn_configuration_id: int, *, for_update: bool = False) -> FakeConfiguration | None:
        del for_update
        return self.configuration if vpn_configuration_id == 30 else None

    async def save_entity(self, configuration: FakeConfiguration) -> None:
        assert configuration is self.configuration
        self.saved += 1


class FakeNodeRepository:
    def __init__(self, task: NodeTask) -> None:
        self.task = task
        self.vpn = FakeVpnRepository()
        self.attempts: dict[int, dict[str, Any]] = {}
        self.finish_calls = 0
        self.touch_success_calls = 0
        self.unreachable_calls = 0

    async def get_task_entity(self, node_task_id: int, *, for_update: bool = False) -> NodeTask | None:
        del for_update
        return self.task if node_task_id == self.task.id else None

    async def save_task_entity(self, task: NodeTask) -> None:
        self.task = task

    async def create_task_attempt(self, *, node_task_id: int, attempt_no: int, request_payload: dict[str, Any], started_at: datetime) -> dict[str, Any]:
        del request_payload
        attempt = {
            "node_task_attempt_id": 100 + attempt_no,
            "node_task_id": node_task_id,
            "attempt_no": attempt_no,
            "status": "started",
            "started_at": started_at,
            "finished_at": None,
        }
        self.attempts[attempt_no] = attempt
        return attempt

    async def finish_task_attempt(self, node_task_attempt_id: int, *, status: str, finished_at: datetime, response_payload: dict[str, Any] | None = None, error_message: str | None = None) -> None:
        del response_payload
        self.finish_calls += 1
        for attempt in self.attempts.values():
            if attempt["node_task_attempt_id"] == node_task_attempt_id:
                attempt.update(status=status, finished_at=finished_at, error_message=error_message)
                return
        raise AssertionError("attempt not found")

    async def fail_started_task_attempt(self, *, node_task_id: int, attempt_no: int, finished_at: datetime, error_message: str) -> bool:
        attempt = self.attempts.get(attempt_no)
        if not attempt or attempt["node_task_id"] != node_task_id or attempt["status"] != "started" or attempt["finished_at"] is not None:
            return False
        attempt.update(status="failed", finished_at=finished_at, error_message=error_message)
        return True

    async def list_stale_task_entities(self, now: datetime, limit: int = 100) -> list[NodeTask]:
        del limit
        return [self.task] if self.task.lease_is_expired(now) else []

    async def list_due_tasks(self, now: datetime, limit: int = 100) -> list[int]:
        del limit
        return [int(self.task.id)] if self.task.can_dispatch(now) else []

    async def get_node(self, node_id: int, *, for_update: bool = False) -> dict[str, Any] | None:
        del for_update
        return {"node_id": node_id, "api_base_url": "http://node"}

    async def get_active_credential(self, node_id: int) -> dict[str, Any]:
        return {"node_id": node_id, "key_id": "key", "shared_secret": "secret"}

    async def touch_node_success(self, node_id: int, *, seen_at: datetime) -> None:
        del node_id, seen_at
        self.touch_success_calls += 1

    async def mark_node_unreachable(self, node_id: int, *, error_message: str, seen_at: datetime) -> None:
        del node_id, error_message, seen_at
        self.unreachable_calls += 1


class FakeUow:
    def __init__(self, nodes: FakeNodeRepository) -> None:
        self.nodes = nodes
        self.vpn = nodes.vpn

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class FakeGateway:
    def __init__(self) -> None:
        self.idempotency_keys: list[str] = []

    async def provision_client(self, *, node: dict[str, Any], credential: dict[str, Any], payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        del node, credential, payload
        self.idempotency_keys.append(idempotency_key)
        return {"status": "provisioned", "remote_client_ref": "remote-1"}

    async def revoke_client(self, *, node: dict[str, Any], credential: dict[str, Any], payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        del node, credential, payload, idempotency_key
        raise AssertionError("not used")


class FakeQueue:
    def __init__(self, *, fail_dispatch: bool = False) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_dispatch = fail_dispatch

    async def enqueue(self, job_name: str, *args: Any) -> None:
        if self.fail_dispatch and job_name == "dispatch_node_task":
            raise RuntimeError("queue unavailable")
        self.jobs.append((job_name, args))


def use_case(task: NodeTask, clock: MutableClock, queue: FakeQueue | None = None) -> tuple[DispatchNodeTask, FakeNodeRepository, FakeGateway, FakeQueue]:
    repo = FakeNodeRepository(task)
    gateway = FakeGateway()
    actual_queue = queue or FakeQueue()
    case = DispatchNodeTask(
        uow_factory=lambda: FakeUow(repo),
        node_gateway=gateway,
        job_queue=actual_queue,
        retry_base_seconds=15,
        lease_seconds=60,
        clock=clock,
    )
    return case, repo, gateway, actual_queue


@pytest.mark.asyncio
async def test_reaper_recovers_crash_window_and_recovered_task_can_succeed() -> None:
    task = pending_task()
    original_key = task.idempotency_key
    clock = MutableClock(START)
    case, repo, gateway, _ = use_case(task, clock)

    prepared = await case._prepare(node_task_id=10, started_at=clock())
    assert isinstance(prepared, PreparedDispatch)
    first_token = prepared.lease_token
    assert repo.task.status is NodeTaskStatus.IN_PROGRESS

    clock.value = START + timedelta(seconds=61)
    result = await case.recover_stale()
    assert result["recovered"] == 1
    assert repo.task.status is NodeTaskStatus.PENDING
    assert repo.task.idempotency_key == original_key
    assert repo.attempts[1]["status"] == "failed"
    assert repo.task.lease_token is None

    clock.value = repo.task.next_retry_at
    second = await case._prepare(node_task_id=10, started_at=clock())
    assert isinstance(second, PreparedDispatch)
    assert second.lease_token != first_token

    finish_calls_before = repo.finish_calls
    stale = await case._finalize(
        prepared=prepared,
        response={"status": "provisioned"},
        request_error=None,
        completed_at=clock(),
    )
    assert stale.status == "stale_attempt"
    assert repo.finish_calls == finish_calls_before
    assert repo.task.status is NodeTaskStatus.IN_PROGRESS
    assert repo.task.lease_token == second.lease_token

    current = await case._finalize(
        prepared=second,
        response={"status": "provisioned", "remote_client_ref": "remote-1"},
        request_error=None,
        completed_at=clock(),
    )
    assert current.status == "provisioned"
    assert repo.task.status is NodeTaskStatus.SUCCEEDED
    assert repo.task.lease_token is None
    assert repo.touch_success_calls == 1
    assert gateway.idempotency_keys == []


@pytest.mark.asyncio
async def test_recovery_enqueue_failure_keeps_durable_pending_task() -> None:
    task = pending_task()
    clock = MutableClock(START)
    queue = FakeQueue(fail_dispatch=True)
    case, repo, _, _ = use_case(task, clock, queue)
    prepared = await case._prepare(node_task_id=10, started_at=clock())
    assert isinstance(prepared, PreparedDispatch)

    clock.value = START + timedelta(seconds=61)
    result = await case.recover_stale()
    assert result["enqueue_failures"] == 1
    assert repo.task.status is NodeTaskStatus.PENDING

    clock.value = repo.task.next_retry_at
    assert await repo.list_due_tasks(clock()) == [10]


@pytest.mark.asyncio
async def test_execute_reuses_original_idempotency_key_after_recovery() -> None:
    task = pending_task()
    clock = MutableClock(START)
    case, repo, gateway, _ = use_case(task, clock)
    prepared = await case._prepare(node_task_id=10, started_at=clock())
    assert isinstance(prepared, PreparedDispatch)
    clock.value = START + timedelta(seconds=61)
    await case.recover_stale()
    clock.value = repo.task.next_retry_at

    result = await case.execute(node_task_id=10)

    assert result["status"] == "provisioned"
    assert gateway.idempotency_keys == ["provision:vpn:10"]


@pytest.mark.asyncio
async def test_reaper_max_attempts_marks_configuration_failed() -> None:
    task = pending_task(max_attempts=1)
    task.vpn_configuration_id = 30
    task.vpn_generation = 1
    clock = MutableClock(START)
    case, repo, _, _ = use_case(task, clock)
    prepared = await case._prepare(node_task_id=10, started_at=clock())
    assert isinstance(prepared, PreparedDispatch)
    clock.value = START + timedelta(seconds=61)

    result = await case.recover_stale()

    assert result["failed"] == 1
    assert repo.task.status is NodeTaskStatus.FAILED
    assert repo.vpn.configuration.provision_failed is True
    assert repo.vpn.saved == 1


@pytest.mark.asyncio
async def test_nonexpired_in_progress_task_is_untouched_by_reaper() -> None:
    task = pending_task()
    clock = MutableClock(START)
    case, repo, _, _ = use_case(task, clock)
    prepared = await case._prepare(node_task_id=10, started_at=clock())
    assert isinstance(prepared, PreparedDispatch)
    token = repo.task.lease_token
    clock.value = START + timedelta(seconds=30)

    result = await case.recover_stale()

    assert result["recovered"] == 0
    assert repo.task.status is NodeTaskStatus.IN_PROGRESS
    assert repo.task.lease_token == token
    assert repo.attempts[1]["status"] == "started"
