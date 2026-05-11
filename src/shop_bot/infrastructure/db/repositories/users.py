from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.infrastructure.db.tables import user_contacts, users


class UserRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def get_user_by_contact(self, contact_type: str, contact_value: str) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(users)
            .join(user_contacts, user_contacts.c.user_id == users.c.user_id)
            .where(
                and_(
                    user_contacts.c.contact_type == contact_type,
                    user_contacts.c.contact_value == contact_value,
                )
            )
            .limit(1)
        )
        result = await self.connection.execute(query)
        row = result.mappings().first()
        return row

    async def create_user(self, name_or_nick: str) -> int:
        statement = users.insert().values(name_or_nick=name_or_nick).returning(users.c.user_id)
        result = await self.connection.execute(statement)
        user_id = result.scalar()
        if user_id is None:
            raise RuntimeError("Failed to create user: INSERT returned no user_id")
        return int(user_id)

    async def upsert_contact(
        self,
        user_id: int,
        contact_type: str,
        contact_value: str,
        *,
        is_primary: bool = True,
    ) -> None:
        statement = (
            pg_insert(user_contacts)
            .values(
                user_id=user_id,
                contact_type=contact_type,
                contact_value=contact_value,
                is_primary=is_primary,
            )
            .on_conflict_do_update(
                index_elements=[user_contacts.c.contact_type, user_contacts.c.contact_value],
                set_={"user_id": user_id, "is_primary": is_primary},
            )
        )
        await self.connection.execute(statement)

    async def register_telegram_user(
        self,
        telegram_id: int,
        username: str | None,
        name_or_nick: str,
    ) -> Mapping[str, Any]:
        existing = await self.get_user_by_contact("telegram_id", str(telegram_id))
        if existing is None:
            user_id = await self.create_user(name_or_nick=name_or_nick)
            await self.upsert_contact(user_id, "telegram_id", str(telegram_id), is_primary=True)
            if username:
                await self.upsert_contact(user_id, "telegram_username", username, is_primary=False)
            return {"user_id": user_id, "name_or_nick": name_or_nick}

        if username:
            await self.upsert_contact(int(existing["user_id"]), "telegram_username", username, is_primary=False)
        return existing