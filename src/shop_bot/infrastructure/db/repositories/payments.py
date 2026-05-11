from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.infrastructure.db.tables import (
    outbox_events,
    payment_attempts,
    payment_events,
    payment_orders,
    provider_transactions,
    webhook_inbox,
)


class PaymentRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def get_order_by_idempotency(self, idempotency_key: str) -> Mapping[str, Any] | None:
        result = await self.connection.execute(
            select(payment_orders).where(payment_orders.c.idempotency_key == idempotency_key).limit(1)
        )
        return result.mappings().first()

    async def create_order(
        self,
        *,
        user_id: int,
        tariff_id: int,
        provider: str,
        status: str,
        amount_minor: int,
        currency: str,
        requested_period_days: int,
        idempotency_key: str,
        metadata: dict[str, Any],
    ) -> Mapping[str, Any]:
        statement = (
            pg_insert(payment_orders)
            .values(
                user_id=user_id,
                tariff_id=tariff_id,
                provider=provider,
                status=status,
                amount_minor=amount_minor,
                currency=currency,
                requested_period_days=requested_period_days,
                idempotency_key=idempotency_key,
                metadata=metadata,
            )
            .on_conflict_do_nothing(index_elements=[payment_orders.c.idempotency_key])
            .returning(payment_orders)
        )
        result = await self.connection.execute(statement)
        row = result.mappings().first()
        if row is not None:
            return row
        existing = await self.get_order_by_idempotency(idempotency_key)
        if existing is None:
            raise RuntimeError("Failed to create payment order")
        return existing

    async def get_order(self, order_id: int, *, for_update: bool = False) -> Mapping[str, Any] | None:
        query: Select[Any] = select(payment_orders).where(payment_orders.c.payment_order_id == order_id).limit(1)
        if for_update:
            query = query.with_for_update()
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def list_orders(self, limit: int = 100, offset: int = 0) -> list[Mapping[str, Any]]:
        result = await self.connection.execute(
            select(payment_orders)
            .order_by(payment_orders.c.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.mappings().all())

    async def create_attempt(
        self,
        *,
        payment_order_id: int,
        provider: str,
        provider_payment_id: str | None,
        status: str,
        payment_url: str | None,
        payload: dict[str, Any],
    ) -> Mapping[str, Any]:
        result = await self.connection.execute(
            payment_attempts.insert()
            .values(
                payment_order_id=payment_order_id,
                provider=provider,
                provider_payment_id=provider_payment_id,
                status=status,
                payment_url=payment_url,
                payload=payload,
            )
            .returning(payment_attempts)
        )
        row = result.mappings().first()
        if row is None:
            raise RuntimeError("Failed to create payment attempt")
        return row

    async def get_latest_attempt(self, payment_order_id: int) -> Mapping[str, Any] | None:
        result = await self.connection.execute(
            select(payment_attempts)
            .where(payment_attempts.c.payment_order_id == payment_order_id)
            .order_by(payment_attempts.c.created_at.desc())
            .limit(1)
        )
        return result.mappings().first()

    async def get_attempt(self, payment_attempt_id: int) -> Mapping[str, Any] | None:
        result = await self.connection.execute(
            select(payment_attempts).where(payment_attempts.c.payment_attempt_id == payment_attempt_id).limit(1)
        )
        return result.mappings().first()

    async def get_attempt_by_provider_payment_id(
        self,
        provider: str,
        provider_payment_id: str,
    ) -> Mapping[str, Any] | None:
        result = await self.connection.execute(
            select(payment_attempts)
            .where(
                payment_attempts.c.provider == provider,
                payment_attempts.c.provider_payment_id == provider_payment_id,
            )
            .limit(1)
        )
        return result.mappings().first()

    async def update_attempt_status(
        self,
        payment_attempt_id: int,
        status: str,
        *,
        provider_payment_id: str | None = None,
        payment_url: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC)}
        if provider_payment_id is not None:
            values["provider_payment_id"] = provider_payment_id
        if payment_url is not None:
            values["payment_url"] = payment_url
        if payload is not None:
            values["payload"] = payload
        await self.connection.execute(
            update(payment_attempts)
            .where(payment_attempts.c.payment_attempt_id == payment_attempt_id)
            .values(**values)
        )

    async def mark_order_paid(self, order_id: int, paid_at: datetime) -> None:
        await self.connection.execute(
            update(payment_orders)
            .where(payment_orders.c.payment_order_id == order_id)
            .values(status="paid", paid_at=paid_at, updated_at=paid_at)
        )

    async def mark_order_status(self, order_id: int, status: str, updated_at: datetime) -> None:
        await self.connection.execute(
            update(payment_orders)
            .where(payment_orders.c.payment_order_id == order_id)
            .values(status=status, updated_at=updated_at)
        )

    async def save_webhook_inbox(
        self,
        *,
        provider: str,
        event_key: str,
        headers: dict[str, Any],
        payload: dict[str, Any],
        status: str,
    ) -> tuple[bool, Mapping[str, Any]]:
        statement = (
            pg_insert(webhook_inbox)
            .values(
                provider=provider,
                event_key=event_key,
                headers=headers,
                payload=payload,
                status=status,
            )
            .on_conflict_do_nothing(index_elements=[webhook_inbox.c.provider, webhook_inbox.c.event_key])
            .returning(webhook_inbox)
        )
        result = await self.connection.execute(statement)
        row = result.mappings().first()
        if row is not None:
            return True, row
        existing_result = await self.connection.execute(
            select(webhook_inbox)
            .where(webhook_inbox.c.provider == provider, webhook_inbox.c.event_key == event_key)
            .limit(1)
        )
        existing = existing_result.mappings().first()
        if existing is None:
            raise RuntimeError("Failed to fetch existing webhook inbox row")
        return False, existing

    async def mark_webhook_inbox_status(
        self,
        webhook_inbox_id: int,
        status: str,
        *,
        processed_at: datetime | None = None,
        error_message: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "error_message": error_message}
        if processed_at is not None:
            values["processed_at"] = processed_at
        await self.connection.execute(
            update(webhook_inbox)
            .where(webhook_inbox.c.webhook_inbox_id == webhook_inbox_id)
            .values(**values)
        )

    async def create_payment_event(
        self,
        *,
        payment_order_id: int | None,
        payment_attempt_id: int | None,
        provider: str,
        event_type: str,
        event_key: str,
        status: str,
        payload: dict[str, Any],
        occurred_at: datetime,
    ) -> Mapping[str, Any]:
        statement = (
            pg_insert(payment_events)
            .values(
                payment_order_id=payment_order_id,
                payment_attempt_id=payment_attempt_id,
                provider=provider,
                event_type=event_type,
                event_key=event_key,
                status=status,
                payload=payload,
                occurred_at=occurred_at,
            )
            .on_conflict_do_nothing(index_elements=[payment_events.c.event_key])
            .returning(payment_events)
        )
        result = await self.connection.execute(statement)
        row = result.mappings().first()
        if row is not None:
            return row
        existing_result = await self.connection.execute(
            select(payment_events).where(payment_events.c.event_key == event_key).limit(1)
        )
        existing = existing_result.mappings().first()
        if existing is None:
            raise RuntimeError("Failed to fetch existing payment event")
        return existing

    async def get_payment_event(self, payment_event_id: int, *, for_update: bool = False) -> Mapping[str, Any] | None:
        query: Select[Any] = select(payment_events).where(payment_events.c.payment_event_id == payment_event_id).limit(1)
        if for_update:
            query = query.with_for_update()
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def mark_payment_event_status(
        self,
        payment_event_id: int,
        status: str,
        *,
        processed_at: datetime | None = None,
        error_message: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "error_message": error_message}
        if processed_at is not None:
            values["processed_at"] = processed_at
        await self.connection.execute(
            update(payment_events)
            .where(payment_events.c.payment_event_id == payment_event_id)
            .values(**values)
        )

    async def create_provider_transaction(
        self,
        *,
        payment_order_id: int,
        payment_attempt_id: int | None,
        provider: str,
        transaction_type: str,
        provider_transaction_key: str,
        transaction_status: str,
        amount_minor: int,
        currency: str,
        payload: dict[str, Any],
    ) -> None:
        statement = (
            pg_insert(provider_transactions)
            .values(
                payment_order_id=payment_order_id,
                payment_attempt_id=payment_attempt_id,
                provider=provider,
                transaction_type=transaction_type,
                provider_transaction_key=provider_transaction_key,
                transaction_status=transaction_status,
                amount_minor=amount_minor,
                currency=currency,
                payload=payload,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    provider_transactions.c.provider,
                    provider_transactions.c.provider_transaction_key,
                ]
            )
        )
        await self.connection.execute(statement)

    async def create_outbox_event(
        self,
        *,
        event_name: str,
        aggregate_type: str,
        aggregate_id: int,
        payload: dict[str, Any],
        status: str = "pending",
    ) -> int:
        result = await self.connection.execute(
            outbox_events.insert()
            .values(
                event_name=event_name,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                status=status,
            )
            .returning(outbox_events.c.outbox_event_id)
        )
        return int(result.scalar_one())

    async def list_pending_outbox_events(self, limit: int = 100) -> list[Mapping[str, Any]]:
        result = await self.connection.execute(
            select(outbox_events)
            .where(outbox_events.c.status == "pending")
            .order_by(outbox_events.c.available_at.asc())
            .limit(limit)
        )
        return list(result.mappings().all())

    async def mark_outbox_published(self, outbox_event_id: int, published_at: datetime) -> None:
        await self.connection.execute(
            update(outbox_events)
            .where(outbox_events.c.outbox_event_id == outbox_event_id)
            .values(status="published", published_at=published_at)
        )

    async def mark_outbox_failed(self, outbox_event_id: int, last_error: str) -> None:
        await self.connection.execute(
            update(outbox_events)
            .where(outbox_events.c.outbox_event_id == outbox_event_id)
            .values(status="failed", attempts=outbox_events.c.attempts + 1, last_error=last_error)
        )
