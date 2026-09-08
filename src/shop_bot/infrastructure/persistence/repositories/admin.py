from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.dialects.postgresql import insert as pg_insert
from shop_bot.core.exceptions import ConflictError

from shop_bot.domain.entities.tariff import Tariff
from shop_bot.infrastructure.persistence.sqlalchemy.mappers import tariff_from_row
from shop_bot.infrastructure.persistence.sqlalchemy.tables import tariff_specs, tariffs


class AdminRepository:
    def __init__(self, connection: AsyncConnection) -> None:
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
            .order_by(tariffs.c.tariff_id.asc())
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
            pg_insert(tariffs)
            .values(tariff_name=tariff_name)
            .on_conflict_do_nothing(index_elements=[tariffs.c.tariff_name])
            .returning(tariffs.c.tariff_id)
        )

        tariff_id_row = result.first()

        if tariff_id_row is None:
            raise ConflictError("Tariff with the same name already exists")

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

        return {
            "tariff_id": tariff_id,
            "tariff_name": tariff_name,
            "price_minor": price_minor,
            "currency": currency,
            "period_days": period_days,
            "description": description,
            "is_enabled": is_enabled,
        }

    async def get_tariff_entity(self, tariff_id: int) -> Tariff | None:
        row = await self.get_tariff(tariff_id)
        return tariff_from_row(row) if row is not None else None
