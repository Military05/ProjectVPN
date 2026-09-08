from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncConnection
from shop_bot.infrastructure.persistence.sqlalchemy.tables import audit_events


class AuditRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def append(self, *, event_name: str, aggregate_type: str, aggregate_id: int, payload: Mapping[str, Any] | None = None, created_at: datetime | None = None) -> int:
        values: dict[str, Any] = {"event_name": event_name, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id, "payload": dict(payload or {})}
        if created_at is not None:
            values["created_at"] = created_at
        result = await self.connection.execute(audit_events.insert().values(**values).returning(audit_events.c.audit_event_id))
        return int(result.scalar_one())
