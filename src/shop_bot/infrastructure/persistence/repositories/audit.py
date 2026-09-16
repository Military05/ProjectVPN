from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.domain.audit import AuditAggregateType, AuditEventName
from shop_bot.infrastructure.persistence.sqlalchemy.tables import audit_events


class AuditRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def append(
        self,
        *,
        event_name: AuditEventName,
        aggregate_type: AuditAggregateType,
        aggregate_id: int,
        payload: Mapping[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> int:
        values: dict[str, Any] = {
            "event_name": event_name.value,
            "aggregate_type": aggregate_type.value,
            "aggregate_id": aggregate_id,
            "payload": dict(payload or {}),
        }
        if created_at is not None:
            values["created_at"] = created_at
        result = await self.connection.execute(
            audit_events.insert()
            .values(**values)
            .returning(audit_events.c.audit_event_id)
        )
        return int(result.scalar_one())
