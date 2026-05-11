from __future__ import annotations

from collections.abc import Mapping

import structlog

from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow

logger = structlog.get_logger(__name__)


async def publish_outbox_events(container: ServiceContainer) -> Mapping[str, int]:
    published = 0
    async with container.uow() as uow:
        events = await uow.payments.list_pending_outbox_events(limit=100)
        for event in events:
            try:
                logger.info(
                    "outbox_event_published",
                    outbox_event_id=int(event["outbox_event_id"]),
                    event_name=str(event["event_name"]),
                    aggregate_type=str(event["aggregate_type"]),
                    aggregate_id=int(event["aggregate_id"]),
                )
                await uow.payments.mark_outbox_published(int(event["outbox_event_id"]), utcnow())
                published += 1
            except Exception as exc:  # pragma: no cover - defensive path
                await uow.payments.mark_outbox_failed(int(event["outbox_event_id"]), str(exc))
    return {"published": published}
