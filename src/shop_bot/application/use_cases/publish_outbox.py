from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import logging

from shop_bot.application.ports import UnitOfWorkFactory

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PublishOutbox:
    uow_factory: UnitOfWorkFactory
    clock: Callable[[], datetime]

    async def execute(self) -> dict[str, int]:
        published = 0
        now = self.clock()
        async with self.uow_factory() as uow:
            events = await uow.payments.list_pending_outbox_events(now, limit=100)
            for event in events:
                try:
                    logger.info(
                        "outbox_event_published id=%s name=%s aggregate=%s:%s",
                        int(event["outbox_event_id"]),
                        str(event["event_name"]),
                        str(event["aggregate_type"]),
                        int(event["aggregate_id"]),
                    )
                    await uow.payments.mark_outbox_published(int(event["outbox_event_id"]), now)
                    published += 1
                except Exception as exc:  # pragma: no cover
                    await uow.payments.reschedule_outbox_event(int(event["outbox_event_id"]), str(exc), now + timedelta(minutes=5))
        return {"published": published}
