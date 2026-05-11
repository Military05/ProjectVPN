from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.infrastructure.db.tables import (
    subscription_periods,
    subscriptions,
    tariff_specs,
    tariffs,
)


class SubscriptionRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def lock_active_subscription_for_user(
        self,
        user_id: int,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(subscriptions)
            .where(
                subscriptions.c.user_id == user_id,
                subscriptions.c.status == "active",
            )
            .with_for_update()
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_subscription(
        self,
        subscription_id: int,
    ) -> Mapping[str, Any] | None:
        result = await self.connection.execute(
            select(subscriptions)
            .where(subscriptions.c.subscription_id == subscription_id)
            .limit(1)
        )
        return result.mappings().first()

    async def create_subscription(
        self,
        user_id: int,
        tariff_id: int,
        status: str,
        created_at: datetime,
    ) -> int:
        statement = (
            subscriptions.insert()
            .values(
                user_id=user_id,
                tariff_id=tariff_id,
                status=status,
                created_at=created_at,
            )
            .returning(subscriptions.c.subscription_id)
        )

        result = await self.connection.execute(statement)
        return int(result.scalar_one())

    async def end_subscription(
        self,
        subscription_id: int,
        ended_at: datetime,
        status: str = "ended",
    ) -> None:
        await self.connection.execute(
            update(subscriptions)
            .where(subscriptions.c.subscription_id == subscription_id)
            .values(
                status=status,
                ended_at=ended_at,
            )
        )

    async def lock_last_period(
        self,
        subscription_id: int,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(subscription_periods)
            .where(subscription_periods.c.subscription_id == subscription_id)
            .order_by(subscription_periods.c.expires_at.desc())
            .with_for_update()
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_latest_paid_period(
        self,
        subscription_id: int,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(subscription_periods)
            .where(
                subscription_periods.c.subscription_id == subscription_id,
                subscription_periods.c.is_paid.is_(True),
            )
            .order_by(subscription_periods.c.expires_at.desc())
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def create_period(
        self,
        subscription_id: int,
        starts_at: datetime,
        expires_at: datetime,
        *,
        is_paid: bool,
        created_at: datetime,
    ) -> int:
        statement = (
            subscription_periods.insert()
            .values(
                subscription_id=subscription_id,
                starts_at=starts_at,
                expires_at=expires_at,
                is_paid=is_paid,
                created_at=created_at,
            )
            .returning(subscription_periods.c.subscription_period_id)
        )

        result = await self.connection.execute(statement)
        return int(result.scalar_one())

    async def get_current_period(
        self,
        subscription_id: int,
        now: datetime,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(subscription_periods)
            .where(
                subscription_periods.c.subscription_id == subscription_id,
                subscription_periods.c.is_paid.is_(True),
                subscription_periods.c.starts_at <= now,
                subscription_periods.c.expires_at > now,
            )
            .order_by(subscription_periods.c.expires_at.desc())
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_current_access_for_user(
        self,
        user_id: int,
        now: datetime,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(
                subscriptions.c.subscription_id,
                subscriptions.c.user_id,
                subscriptions.c.tariff_id,
                subscriptions.c.status,
                subscriptions.c.created_at,
                subscriptions.c.ended_at,
                subscription_periods.c.subscription_period_id,
                subscription_periods.c.starts_at,
                subscription_periods.c.expires_at,
                tariffs.c.tariff_name,
                tariff_specs.c.price_minor,
                tariff_specs.c.currency,
                tariff_specs.c.period_days,
                tariff_specs.c.description,
            )
            .join(
                subscription_periods,
                subscription_periods.c.subscription_id == subscriptions.c.subscription_id,
            )
            .join(
                tariffs,
                tariffs.c.tariff_id == subscriptions.c.tariff_id,
            )
            .join(
                tariff_specs,
                tariff_specs.c.tariff_id == tariffs.c.tariff_id,
            )
            .where(
                subscriptions.c.user_id == user_id,
                subscriptions.c.status == "active",
                subscription_periods.c.is_paid.is_(True),
                subscription_periods.c.starts_at <= now,
                subscription_periods.c.expires_at > now,
            )
            .order_by(subscription_periods.c.expires_at.desc())
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def list_subscriptions(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Mapping[str, Any]]:
        query: Select[Any] = (
            select(
                subscriptions.c.subscription_id,
                subscriptions.c.user_id,
                subscriptions.c.tariff_id,
                subscriptions.c.status,
                subscriptions.c.created_at,
                subscriptions.c.ended_at,
                tariffs.c.tariff_name,
            )
            .join(
                tariffs,
                tariffs.c.tariff_id == subscriptions.c.tariff_id,
            )
            .order_by(subscriptions.c.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.connection.execute(query)
        return list(result.mappings().all())

    async def list_expired_active_subscription_ids(
        self,
        now: datetime,
    ) -> list[int]:
        query: Select[Any] = (
            select(subscriptions.c.subscription_id)
            .where(subscriptions.c.status == "active")
            .where(
                ~subscriptions.c.subscription_id.in_(
                    select(subscription_periods.c.subscription_id).where(
                        subscription_periods.c.is_paid.is_(True),
                        subscription_periods.c.starts_at <= now,
                        subscription_periods.c.expires_at > now,
                    )
                )
            )
        )
        result = await self.connection.execute(query)
        return [int(value) for value in result.scalars().all()]

    async def list_due_subscriptions_for_provision(
        self,
        now: datetime,
    ) -> list[int]:
        query: Select[Any] = (
            select(subscriptions.c.subscription_id)
            .join(
                subscription_periods,
                subscription_periods.c.subscription_id == subscriptions.c.subscription_id,
            )
            .where(
                subscriptions.c.status == "active",
                subscription_periods.c.is_paid.is_(True),
                subscription_periods.c.starts_at <= now,
                subscription_periods.c.expires_at > now,
            )
            .distinct()
        )
        result = await self.connection.execute(query)
        return [int(value) for value in result.scalars().all()]