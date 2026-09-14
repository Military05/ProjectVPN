from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from shop_bot.application.commands.publish_outbox_events import publish_outbox_events
from shop_bot.application.commands.recover_payment_events import recover_payment_events
from shop_bot.application.job_names import JobName


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


class PoisonContainer:
    def __getattribute__(self, name: str) -> Any:
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError(f"legacy tombstone touched container.{name}")


@pytest.mark.asyncio
async def test_stale_publish_outbox_job_is_database_free_deprecated_noop() -> None:
    assert await publish_outbox_events(PoisonContainer()) == {
        "status": "deprecated_noop"
    }
