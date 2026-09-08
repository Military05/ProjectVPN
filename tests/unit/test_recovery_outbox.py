from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from shop_bot.application.commands.recover_payment_events import recover_payment_events
from shop_bot.application.job_names import JobName
from shop_bot.application.use_cases.publish_outbox import PublishOutbox

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class Queue:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, tuple[Any, ...]]] = []

    async def enqueue(self, name: Any, *args: Any) -> None:
        self.jobs.append((name, args))


class RecoveryPayments:
    async def list_received_payment_event_ids(self, limit: int = 100) -> list[int]:
        assert limit == 100
        return [11, 12]


class RecoveryUow:
    payments = RecoveryPayments()
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None


@pytest.mark.asyncio
async def test_received_payment_recovery_requeues_durable_events() -> None:
    queue = Queue()
    container = SimpleNamespace(uow=lambda: RecoveryUow(), job_queue=queue)
    result = await recover_payment_events(container)
    assert result == {"enqueued": 2}
    assert queue.jobs == [
        (JobName.PROCESS_PAYMENT_EVENT, (11,)),
        (JobName.PROCESS_PAYMENT_EVENT, (12,)),
    ]


class OutboxPayments:
    def __init__(self) -> None:
        self.rescheduled: list[tuple[int, str, datetime]] = []
        self.published: list[int] = []
        self.fail_once = True

    async def list_pending_outbox_events(self, now: datetime, limit: int = 100):
        assert now == NOW and limit == 100
        return [{"outbox_event_id": 5, "event_name": "x", "aggregate_type": "order", "aggregate_id": 1}]

    async def mark_outbox_published(self, event_id: int, published_at: datetime) -> None:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary persistence failure")
        self.published.append(event_id)

    async def reschedule_outbox_event(self, event_id: int, error: str, available_at: datetime) -> None:
        self.rescheduled.append((event_id, error, available_at))


class OutboxUow:
    def __init__(self, repo: OutboxPayments): self.payments = repo
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None


@pytest.mark.asyncio
async def test_outbox_failure_is_rescheduled_instead_of_becoming_stuck() -> None:
    repo = OutboxPayments()
    use_case = PublishOutbox(uow_factory=lambda: OutboxUow(repo), clock=lambda: NOW)
    assert await use_case.execute() == {"published": 0}
    assert repo.rescheduled == [(5, "temporary persistence failure", NOW + timedelta(minutes=5))]
