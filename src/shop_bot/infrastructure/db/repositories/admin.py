from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.infrastructure.db.tables import tariff_specs, tariffs


class AdminRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def list_tariffs(self, *, include_disabled: bool = True) -> list[Mapping[str, Any]]:
        query: Select[Any] = (
            select(
                tariffs.c.tariff_id,
                tariffs.c.tariff_name,
                tariff_specs.c.price_minor,
                tariff_specs.c.currency,
                tariff_specs.c.period_days,
                tariff_specs.c.description,
                tariff_specs.c.is_enabled,
            )
            .join(tariff_specs, tariff_specs.c.tariff_id == tariffs.c.tariff_id)
            .order_by(
                tariff_specs.c.price_minor.asc(),
                tariff_specs.c.period_days.asc(),
                tariffs.c.tariff_id.asc(),
            )
        )

        if not include_disabled:
            query = query.where(tariff_specs.c.is_enabled.is_(True))

        result = await self.connection.execute(query)
        return list(result.mappings().all())

    async def get_tariff(self, tariff_id: int) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(
                tariffs.c.tariff_id,
                tariffs.c.tariff_name,
                tariff_specs.c.price_minor,
                tariff_specs.c.currency,
                tariff_specs.c.period_days,
                tariff_specs.c.description,
                tariff_specs.c.is_enabled,
            )
            .join(tariff_specs, tariff_specs.c.tariff_id == tariffs.c.tariff_id)
            .where(tariffs.c.tariff_id == tariff_id)
            .limit(1)
        )

        result = await self.connection.execute(query)
        return result.mappings().first()

    async def create_tariff(
        self,
        tariff_name: str,
        price_minor: int,
        currency: str,
        period_days: int,
        description: str | None,
        is_enabled: bool = True,
    ) -> Mapping[str, Any]:
        result = await self.connection.execute(
            tariffs.insert()
            .values(tariff_name=tariff_name)
            .returning(tariffs.c.tariff_id)
        )

        tariff_id_row = result.first()

        if tariff_id_row is None:
            result = await self.connection.execute(
                select(tariffs.c.tariff_id)
                .where(tariffs.c.tariff_name == tariff_name)
                .order_by(tariffs.c.tariff_id.desc())
                .limit(1)
            )
            tariff_id_row = result.first()

        if tariff_id_row is None:
            raise RuntimeError("Failed to create tariff: tariff_id was not returned")

        tariff_id = int(tariff_id_row[0])

        await self.connection.execute(
            tariff_specs.insert().values(
                tariff_id=tariff_id,
                price_minor=price_minor,
                currency=currency,
                period_days=period_days,
                description=description,
                is_enabled=is_enabled,
            )
        )

        created = await self.get_tariff(tariff_id)
        if created is None:
            raise RuntimeError("Failed to read created tariff")

        return created

    async def update_tariff(
        self,
        tariff_id: int,
        *,
        tariff_name: str,
        price_minor: int,
        currency: str,
        period_days: int,
        description: str | None,
        is_enabled: bool,
    ) -> Mapping[str, Any] | None:
        existing = await self.get_tariff(tariff_id)
        if existing is None:
            return None

        await self.connection.execute(
            update(tariffs)
            .where(tariffs.c.tariff_id == tariff_id)
            .values(tariff_name=tariff_name)
        )

        await self.connection.execute(
            update(tariff_specs)
            .where(tariff_specs.c.tariff_id == tariff_id)
            .values(
                price_minor=price_minor,
                currency=currency,
                period_days=period_days,
                description=description,
                is_enabled=is_enabled,
            )
        )

        updated = await self.get_tariff(tariff_id)
        if updated is None:
            raise RuntimeError("Failed to read updated tariff")

        return updated

    async def set_tariff_enabled(
        self,
        tariff_id: int,
        *,
        is_enabled: bool,
    ) -> Mapping[str, Any] | None:
        existing = await self.get_tariff(tariff_id)
        if existing is None:
            return None

        await self.connection.execute(
            update(tariff_specs)
            .where(tariff_specs.c.tariff_id == tariff_id)
            .values(is_enabled=is_enabled)
        )

        updated = await self.get_tariff(tariff_id)
        if updated is None:
            raise RuntimeError("Failed to read updated tariff")

        return updated