from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shop_bot.application.job_names import JobName


async def recover_payment_events(container: Any, *, limit: int = 100) -> Mapping[str, int]:
    async with container.uow() as uow:
        event_ids = await uow.payments.list_received_payment_event_ids(limit=limit)
    for event_id in event_ids:
        await container.job_queue.enqueue(JobName.PROCESS_PAYMENT_EVENT, event_id)
    return {"enqueued": len(event_ids)}
