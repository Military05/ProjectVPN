from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from shop_bot.application.commands.retire_node_journal_records import (
    retire_node_journal_records,
)
from shop_bot.application.use_cases.retire_node_journal_records import (
    MAX_RETIREMENT_BATCH_SIZE,
    RetireNodeJournalRecords,
)
from shop_bot.core.config import Settings
from shop_bot.domain.entities.node import NodeTask, NodeTaskOperation, NodeTaskStatus
from shop_bot.infrastructure.nodes.client import NodeApiClient
from shop_bot.infrastructure.persistence.repositories.nodes import NodeRepository


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def terminal_task(
    task_id: int,
    *,
    status: NodeTaskStatus = NodeTaskStatus.SUCCEEDED,
    node_id: int = 1,
) -> NodeTask:
    return NodeTask(
        id=task_id,
        task_uuid=uuid4(),
        node_id=node_id,
        operation=NodeTaskOperation.REVOKE_CLIENT,
        status=status,
        idempotency_key=f"node-task:{task_id}",
    )


class Nodes:
    def __init__(self, tasks: list[NodeTask]) -> None:
        self.tasks = tasks
        self.list_limits: list[int] = []
        self.mark_calls: list[dict[str, Any]] = []
        self.nodes = {
            task.node_id: {
                "node_id": task.node_id,
                "node_key": f"node-{task.node_id}",
                "api_base_url": f"https://node-{task.node_id}.test",
            }
            for task in tasks
        }
        self.credentials = {
            task.node_id: {
                "node_id": task.node_id,
                "key_id": "default",
                "shared_secret": "secret",
            }
            for task in tasks
        }

    async def list_terminal_unretired_tasks(self, limit: int) -> list[NodeTask]:
        self.list_limits.append(limit)
        return [
            task
            for task in self.tasks
            if task.status
            in {NodeTaskStatus.SUCCEEDED, NodeTaskStatus.FAILED, NodeTaskStatus.CANCELLED}
            and task.journal_retired_at is None
        ][:limit]

    async def get_node(
        self, node_id: int, *, for_update: bool = False
    ) -> Mapping[str, Any] | None:
        assert for_update is False
        return self.nodes.get(node_id)

    async def get_active_credential(self, node_id: int) -> Mapping[str, Any] | None:
        return self.credentials.get(node_id)

    async def mark_task_journal_retired(self, **kwargs: Any) -> bool:
        self.mark_calls.append(kwargs)
        for task in self.tasks:
            if (
                task.id == kwargs["node_task_id"]
                and task.idempotency_key == kwargs["idempotency_key"]
                and task.status
                in {
                    NodeTaskStatus.SUCCEEDED,
                    NodeTaskStatus.FAILED,
                    NodeTaskStatus.CANCELLED,
                }
                and task.journal_retired_at is None
            ):
                task.journal_retired_at = kwargs["retired_at"]
                return True
        return False


class TransactionTracker:
    def __init__(self) -> None:
        self.active = 0
        self.entries = 0


class Uow:
    def __init__(self, nodes: Nodes, tracker: TransactionTracker) -> None:
        self.nodes = nodes
        self.tracker = tracker

    async def __aenter__(self) -> "Uow":
        self.tracker.active += 1
        self.tracker.entries += 1
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.tracker.active -= 1


class Gateway:
    def __init__(
        self,
        tracker: TransactionTracker,
        responses: dict[str, str | Exception],
    ) -> None:
        self.tracker = tracker
        self.responses = responses
        self.calls: list[str] = []

    async def retire_journal_record(self, **kwargs: Any) -> dict[str, Any]:
        assert self.tracker.active == 0, "remote I/O must run without a DB transaction"
        key = str(kwargs["idempotency_key"])
        self.calls.append(key)
        response = self.responses[key]
        if isinstance(response, Exception):
            raise response
        return {"status": response}


def build_case(
    tasks: list[NodeTask],
    responses: dict[str, str | Exception],
    *,
    batch_size: int = MAX_RETIREMENT_BATCH_SIZE,
) -> tuple[RetireNodeJournalRecords, Nodes, Gateway, TransactionTracker]:
    nodes = Nodes(tasks)
    tracker = TransactionTracker()
    gateway = Gateway(tracker, responses)
    case = RetireNodeJournalRecords(
        uow_factory=lambda: Uow(nodes, tracker),
        node_gateway=gateway,
        clock=lambda: NOW,
        batch_size=batch_size,
    )
    return case, nodes, gateway, tracker


@pytest.mark.asyncio
async def test_two_phase_retirement_marks_only_confirmed_remote_deletions() -> None:
    tasks = [terminal_task(task_id, node_id=task_id) for task_id in range(1, 7)]
    case, nodes, gateway, tracker = build_case(
        tasks,
        {
            "node-task:1": "retired",
            "node-task:2": "already_retired",
            "node-task:3": "operation_in_progress",
            "node-task:4": "unexpected",
            "node-task:5": RuntimeError("node unavailable"),
            "node-task:6": "retired",
        },
    )
    nodes.credentials.pop(tasks[5].node_id)

    result = await case.execute()

    assert result == {
        "status": "completed",
        "selected": 6,
        "retired": 1,
        "already_retired": 1,
        "operation_in_progress": 1,
        "marked": 2,
        "stale": 0,
        "failed": 3,
    }
    assert gateway.calls == [f"node-task:{task_id}" for task_id in range(1, 6)]
    assert [call["node_task_id"] for call in nodes.mark_calls] == [1, 2]
    assert tasks[0].journal_retired_at == tasks[1].journal_retired_at == NOW
    assert all(task.journal_retired_at is None for task in tasks[2:])
    assert tracker.active == 0
    assert tracker.entries == 3  # one selection + one fenced mark per success


@pytest.mark.asyncio
async def test_lost_success_response_converges_via_already_retired_retry() -> None:
    task = terminal_task(10)
    case, nodes, gateway, _ = build_case(
        [task],
        {task.idempotency_key: RuntimeError("response lost after delete")},
    )

    first = await case.execute()
    assert first["failed"] == 1
    assert task.journal_retired_at is None

    gateway.responses[task.idempotency_key] = "already_retired"
    second = await case.execute()
    assert second["already_retired"] == 1
    assert second["marked"] == 1
    assert task.journal_retired_at == NOW
    assert len(nodes.mark_calls) == 1


@pytest.mark.asyncio
async def test_post_remote_mark_rechecks_terminal_status_and_idempotency_key() -> None:
    task = terminal_task(20)
    case, nodes, gateway, _ = build_case([task], {task.idempotency_key: "retired"})
    original_method = gateway.retire_journal_record

    async def retire_then_change(**kwargs: Any) -> dict[str, Any]:
        response = await original_method(**kwargs)
        task.status = NodeTaskStatus.IN_PROGRESS
        task.idempotency_key = "replacement-key"
        return response

    gateway.retire_journal_record = retire_then_change  # type: ignore[method-assign]

    result = await case.execute()

    assert result["retired"] == 1
    assert result["marked"] == 0
    assert result["stale"] == 1
    assert task.journal_retired_at is None
    assert nodes.mark_calls[0]["idempotency_key"] == "node-task:20"


def test_retirement_batch_is_hard_bounded_to_one_hundred() -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        build_case([], {}, batch_size=101)
    with pytest.raises(ValueError, match="between 1 and 100"):
        build_case([], {}, batch_size=0)


@pytest.mark.asyncio
async def test_repository_selection_and_fenced_mark_match_partial_index_contract() -> None:
    class Result:
        def mappings(self) -> "Result":
            return self

        def all(self) -> list[dict[str, Any]]:
            return []

        def scalar_one_or_none(self) -> int:
            return 1

    class Connection:
        def __init__(self) -> None:
            self.statements: list[Any] = []

        async def execute(self, statement: Any) -> Result:
            self.statements.append(statement)
            return Result()

    connection = Connection()
    repository = NodeRepository(connection)  # type: ignore[arg-type]

    assert await repository.list_terminal_unretired_tasks(limit=100) == []
    assert await repository.mark_task_journal_retired(
        node_task_id=7,
        idempotency_key="node-task:7",
        retired_at=NOW,
    )

    select_sql = str(
        connection.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    update_sql = str(connection.statements[1].compile(dialect=postgresql.dialect()))
    assert "node_tasks.status IN ('succeeded', 'failed', 'cancelled')" in select_sql
    assert "node_tasks.journal_retired_at IS NULL" in select_sql
    assert "ORDER BY node_tasks.node_task_id ASC" in select_sql
    assert "LIMIT 100" in select_sql
    assert "node_tasks.idempotency_key =" in update_sql
    assert "node_tasks.status IN" in update_sql
    assert "node_tasks.journal_retired_at IS NULL" in update_sql
    assert "RETURNING node_tasks.node_task_id" in update_sql


@pytest.mark.asyncio
async def test_command_delegates_to_central_retirement_use_case() -> None:
    expected = {"status": "completed", "selected": 0}

    class Case:
        async def execute(self) -> dict[str, int | str]:
            return expected

    container = SimpleNamespace(
        applications=SimpleNamespace(retire_node_journal_records=Case())
    )
    assert await retire_node_journal_records(container) is expected


@pytest.mark.asyncio
async def test_node_client_calls_authenticated_retirement_route_and_normalizes_busy_409() -> None:
    client = NodeApiClient(Settings())
    calls: list[dict[str, Any]] = []

    async def busy_request(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        request = httpx.Request("POST", "https://node.test/agent/idempotency/retire")
        response = httpx.Response(
            409,
            request=request,
            json={"detail": "operation_in_progress"},
        )
        raise httpx.HTTPStatusError("busy", request=request, response=response)

    client._request = busy_request  # type: ignore[method-assign]
    result = await client.retire_journal_record(
        node={"node_id": 1, "node_key": "node-1", "api_base_url": "https://node.test"},
        credential={"key_id": "default", "shared_secret": "secret"},
        idempotency_key="node-task:1",
    )

    assert result == {"status": "operation_in_progress"}
    assert calls == [
        {
            "node": {
                "node_id": 1,
                "node_key": "node-1",
                "api_base_url": "https://node.test",
            },
            "credential": {"key_id": "default", "shared_secret": "secret"},
            "method": "POST",
            "path": "/agent/idempotency/retire",
            "payload": {"idempotency_key": "node-task:1"},
            "idempotency_key": "node-task:1",
        }
    ]


def test_worker_schedules_retirement_and_node_agent_has_no_ttl_pruning() -> None:
    worker_source = (ROOT / "src/shop_bot/apps/worker/main.py").read_text(
        encoding="utf-8"
    )
    agent_source = (ROOT / "src/shop_bot/apps/node_agent/main.py").read_text(
        encoding="utf-8"
    )
    journal_source = (
        ROOT / "src/shop_bot/infrastructure/nodes/idempotency.py"
    ).read_text(encoding="utf-8")
    config_source = (ROOT / "src/shop_bot/core/config.py").read_text(encoding="utf-8")

    assert "retire_node_journal_records_job," in worker_source
    assert (
        "cron(retire_node_journal_records_job, minute=set(range(60)))"
        in worker_source
    )
    for source in (agent_source, journal_source, config_source):
        assert "prune_completed" not in source
        assert "idempotency_retention" not in source
