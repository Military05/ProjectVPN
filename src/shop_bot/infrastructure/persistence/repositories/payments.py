from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.domain.entities.payment import PaymentAttempt, PaymentEvent, PaymentOrder
from shop_bot.infrastructure.persistence.sqlalchemy.mappers import (
    payment_attempt_from_row,
    payment_event_from_row,
    payment_order_from_row,
)
from shop_bot.infrastructure.persistence.sqlalchemy.tables import (
    outbox_events,
    payment_attempts,
    payment_events,
    payment_orders,
    provider_transactions,
    webhook_inbox,
)


class PaymentRepository:
    def __init__(self, connection: AsyncConnection) -> None:
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
            .order_by(payment_orders.c.created_at.desc(), payment_orders.c.payment_order_id.desc())
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
        creation_claimed_at: datetime | None = None,
        creation_lease_expires_at: datetime | None = None,
        creation_lease_token: Any | None = None,
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
                creation_claimed_at=creation_claimed_at,
                creation_lease_expires_at=creation_lease_expires_at,
                creation_lease_token=creation_lease_token,
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
            .order_by(payment_attempts.c.created_at.desc(), payment_attempts.c.payment_attempt_id.desc())
            .limit(1)
        )
        return result.mappings().first()

    async def get_attempt(
        self, payment_attempt_id: int, *, for_update: bool = False
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(payment_attempts)
            .where(payment_attempts.c.payment_attempt_id == payment_attempt_id)
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        result = await self.connection.execute(query)
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
        creation_claimed_at: datetime | None = None,
        creation_lease_expires_at: datetime | None = None,
        creation_lease_token: Any | None = None,
        clear_creation_claim: bool = False,
        updated_at: datetime | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": updated_at or datetime.now(UTC)}
        if provider_payment_id is not None:
            values["provider_payment_id"] = provider_payment_id
        if payment_url is not None:
            values["payment_url"] = payment_url
        if payload is not None:
            values["payload"] = payload
        if clear_creation_claim:
            values.update(
                creation_claimed_at=None,
                creation_lease_expires_at=None,
                creation_lease_token=None,
            )
        else:
            values.update(
                creation_claimed_at=creation_claimed_at,
                creation_lease_expires_at=creation_lease_expires_at,
                creation_lease_token=creation_lease_token,
            )
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


    async def list_received_payment_event_ids(self, limit: int = 100) -> list[int]:
        result = await self.connection.execute(
            select(payment_events.c.payment_event_id)
            .where(payment_events.c.status == "received")
            .order_by(payment_events.c.received_at.asc(), payment_events.c.payment_event_id.asc())
            .limit(limit)
        )
        return [int(value) for value in result.scalars().all()]

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
        provider_payment_id: str | None = None,
        reported_payment_order_id: int | None = None,
        payment_status: str | None = None,
        amount_minor: int | None = None,
        currency: str | None = None,
    ) -> Mapping[str, Any]:
        statement = (
            pg_insert(payment_events)
            .values(
                payment_order_id=payment_order_id,
                payment_attempt_id=payment_attempt_id,
                provider=provider,
                event_type=event_type,
                event_key=event_key,
                provider_payment_id=provider_payment_id,
                reported_payment_order_id=reported_payment_order_id,
                payment_status=payment_status,
                amount_minor=amount_minor,
                currency=currency,
                status=status,
                payload=payload,
                occurred_at=occurred_at,
            )
            .on_conflict_do_nothing(index_elements=[payment_events.c.provider, payment_events.c.event_key])
            .returning(payment_events)
        )
        result = await self.connection.execute(statement)
        row = result.mappings().first()
        if row is not None:
            return row
        existing_result = await self.connection.execute(
            select(payment_events).where(payment_events.c.provider == provider, payment_events.c.event_key == event_key).limit(1)
        )
        existing = existing_result.mappings().first()
        if existing is None:
            # Compatibility with pre-0009 global uniqueness during rolling deploy.
            existing = (await self.connection.execute(select(payment_events).where(payment_events.c.event_key == event_key).limit(1))).mappings().first()
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

    async def sum_provider_transactions(
        self, *, payment_order_id: int, provider: str, transaction_type: str, transaction_status: str
    ) -> int:
        result = await self.connection.execute(
            select(func.coalesce(func.sum(provider_transactions.c.amount_minor), 0)).where(
                provider_transactions.c.payment_order_id == payment_order_id,
                provider_transactions.c.provider == provider,
                provider_transactions.c.transaction_type == transaction_type,
                provider_transactions.c.transaction_status == transaction_status,
            )
        )
        return int(result.scalar_one())

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

    async def list_pending_outbox_events(self, now: datetime, limit: int = 100) -> list[Mapping[str, Any]]:
        result = await self.connection.execute(
            select(outbox_events)
            .where(outbox_events.c.status == "pending", outbox_events.c.available_at <= now)
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

    async def reschedule_outbox_event(self, outbox_event_id: int, last_error: str, available_at: datetime) -> None:
        await self.connection.execute(
            update(outbox_events)
            .where(outbox_events.c.outbox_event_id == outbox_event_id)
            .values(status="pending", attempts=outbox_events.c.attempts + 1, last_error=last_error, available_at=available_at)
        )

    async def get_order_entity(self, order_id: int, *, for_update: bool = False) -> PaymentOrder | None:
        row = await self.get_order(order_id, for_update=for_update)
        return payment_order_from_row(row) if row is not None else None

    async def get_order_entity_by_idempotency(self, idempotency_key: str) -> PaymentOrder | None:
        row = await self.get_order_by_idempotency(idempotency_key)
        return payment_order_from_row(row) if row is not None else None

    async def add_order_entity(self, order: PaymentOrder) -> PaymentOrder:
        row = await self.create_order(
            user_id=order.user_id,
            tariff_id=order.tariff_id,
            provider=order.provider,
            status=str(order.status),
            amount_minor=order.amount.minor,
            currency=order.amount.currency,
            requested_period_days=order.requested_period_days,
            idempotency_key=order.idempotency_key,
            metadata=order.metadata,
        )
        return payment_order_from_row(row)

    async def save_order_entity(self, order: PaymentOrder) -> None:
        if order.id is None:
            raise ValueError("Cannot save payment order without id")
        await self.connection.execute(
            update(payment_orders)
            .where(payment_orders.c.payment_order_id == order.id)
            .values(
                status=str(order.status),
                updated_at=order.updated_at or datetime.now(UTC),
                paid_at=order.paid_at,
                metadata=order.metadata,
            )
        )

    async def get_attempt_entity(
        self, payment_attempt_id: int, *, for_update: bool = False
    ) -> PaymentAttempt | None:
        row = await self.get_attempt(payment_attempt_id, for_update=for_update)
        return payment_attempt_from_row(row) if row is not None else None

    async def get_latest_attempt_entity(self, payment_order_id: int) -> PaymentAttempt | None:
        row = await self.get_latest_attempt(payment_order_id)
        return payment_attempt_from_row(row) if row is not None else None

    async def get_attempt_entity_by_provider_payment_id(
        self,
        provider: str,
        provider_payment_id: str,
    ) -> PaymentAttempt | None:
        row = await self.get_attempt_by_provider_payment_id(provider, provider_payment_id)
        return payment_attempt_from_row(row) if row is not None else None

    async def add_attempt_entity(self, attempt: PaymentAttempt) -> PaymentAttempt:
        row = await self.create_attempt(
            payment_order_id=attempt.payment_order_id,
            provider=attempt.provider,
            provider_payment_id=attempt.provider_payment_id,
            status=str(attempt.status),
            payment_url=attempt.payment_url,
            payload=attempt.payload,
            creation_claimed_at=attempt.creation_claimed_at,
            creation_lease_expires_at=attempt.creation_lease_expires_at,
            creation_lease_token=attempt.creation_lease_token,
        )
        return payment_attempt_from_row(row)

    async def save_attempt_entity(self, attempt: PaymentAttempt) -> None:
        if attempt.id is None:
            raise ValueError("Cannot save payment attempt without id")
        await self.update_attempt_status(
            attempt.id,
            str(attempt.status),
            provider_payment_id=attempt.provider_payment_id,
            payment_url=attempt.payment_url,
            payload=attempt.payload,
            creation_claimed_at=attempt.creation_claimed_at,
            creation_lease_expires_at=attempt.creation_lease_expires_at,
            creation_lease_token=attempt.creation_lease_token,
            clear_creation_claim=attempt.creation_lease_token is None,
            updated_at=attempt.updated_at,
        )

    async def get_event_entity(self, payment_event_id: int, *, for_update: bool = False) -> PaymentEvent | None:
        row = await self.get_payment_event(payment_event_id, for_update=for_update)
        return payment_event_from_row(row) if row is not None else None

    async def save_event_entity(self, event: PaymentEvent) -> None:
        if event.id is None:
            raise ValueError("Cannot save payment event without id")
        await self.mark_payment_event_status(
            event.id,
            str(event.status),
            processed_at=event.processed_at,
            error_message=event.error_message,
        )
