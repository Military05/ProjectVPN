from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from shop_bot.application.ports import NodeGateway, UnitOfWorkFactory


MAX_RETIREMENT_BATCH_SIZE = 100
SUCCESSFUL_RETIREMENT_STATUSES = frozenset({"retired", "already_retired"})


@dataclass(frozen=True, slots=True)
class JournalRetirementCandidate:
    node_task_id: int
    idempotency_key: str
    node: Mapping[str, Any] | None
    credential: Mapping[str, Any] | None


@dataclass(slots=True)
class RetireNodeJournalRecords:
    uow_factory: UnitOfWorkFactory
    node_gateway: NodeGateway
    clock: Callable[[], datetime]
    batch_size: int = MAX_RETIREMENT_BATCH_SIZE

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= MAX_RETIREMENT_BATCH_SIZE:
            raise ValueError(
                f"batch_size must be between 1 and {MAX_RETIREMENT_BATCH_SIZE}"
            )

    async def execute(self) -> dict[str, int | str]:
        candidates = await self._load_candidates()
        result = {
            "status": "completed",
            "selected": len(candidates),
            "retired": 0,
            "already_retired": 0,
            "operation_in_progress": 0,
            "marked": 0,
            "stale": 0,
            "failed": 0,
        }

        for candidate in candidates:
            if candidate.node is None or candidate.credential is None:
                result["failed"] += 1
                continue
            try:
                response = await self.node_gateway.retire_journal_record(
                    node=candidate.node,
                    credential=candidate.credential,
                    idempotency_key=candidate.idempotency_key,
                )
            except Exception:
                result["failed"] += 1
                continue

            retirement_status = str(response.get("status") or "")
            if retirement_status == "operation_in_progress":
                result["operation_in_progress"] += 1
                continue
            if retirement_status not in SUCCESSFUL_RETIREMENT_STATUSES:
                result["failed"] += 1
                continue

            result[retirement_status] += 1
            async with self.uow_factory() as uow:
                marked = await uow.nodes.mark_task_journal_retired(
                    node_task_id=candidate.node_task_id,
                    idempotency_key=candidate.idempotency_key,
                    retired_at=self.clock(),
                )
            result["marked" if marked else "stale"] += 1

        return result

    async def _load_candidates(self) -> list[JournalRetirementCandidate]:
        candidates: list[JournalRetirementCandidate] = []
        async with self.uow_factory() as uow:
            tasks = await uow.nodes.list_terminal_unretired_tasks(limit=self.batch_size)
            for task in tasks:
                if task.id is None:
                    raise RuntimeError("Persisted NodeTask is missing its primary key")
                node = await uow.nodes.get_node(task.node_id)
                credential = (
                    await uow.nodes.get_active_credential(task.node_id)
                    if node is not None
                    else None
                )
                candidates.append(
                    JournalRetirementCandidate(
                        node_task_id=task.id,
                        idempotency_key=task.idempotency_key,
                        node=node,
                        credential=credential,
                    )
                )
        return candidates
