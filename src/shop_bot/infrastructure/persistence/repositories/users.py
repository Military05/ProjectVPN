from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.domain.entities.user import User
from shop_bot.infrastructure.persistence.sqlalchemy.mappers import user_from_row
from shop_bot.infrastructure.persistence.sqlalchemy.tables import user_contacts, users


class UserRepository:
    def __init__(self, connection: AsyncConnection) -> None:
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


    async def claim_telegram_id_contact(self, user_id: int, telegram_id: int) -> bool:
        statement = (
            pg_insert(user_contacts)
            .values(user_id=user_id, contact_type="telegram_id", contact_value=str(telegram_id), is_primary=True)
            .on_conflict_do_nothing(index_elements=[user_contacts.c.contact_type, user_contacts.c.contact_value])
            .returning(user_contacts.c.user_contact_id)
        )
        result = await self.connection.execute(statement)
        return result.scalar_one_or_none() is not None

    async def register_telegram_user(
        self,
        telegram_id: int,
        username: str | None,
        name_or_nick: str,
    ) -> Mapping[str, Any]:
        existing = await self.get_user_by_contact("telegram_id", str(telegram_id))
        if existing is not None:
            if username:
                await self.upsert_contact(int(existing["user_id"]), "telegram_username", username, is_primary=False)
            return existing

        user_id = await self.create_user(name_or_nick=name_or_nick)
        if await self.claim_telegram_id_contact(user_id, telegram_id):
            if username:
                await self.upsert_contact(user_id, "telegram_username", username, is_primary=False)
            return {"user_id": user_id, "name_or_nick": name_or_nick}

        # Another transaction won the unique telegram-id claim. The just-created
        # user has not been published anywhere, so remove it and return the owner.
        await self.connection.execute(delete(users).where(users.c.user_id == user_id))
        canonical = await self.get_user_by_contact("telegram_id", str(telegram_id))
        if canonical is None:
            raise RuntimeError("Telegram contact claim was lost but canonical owner is missing")
        if username:
            await self.upsert_contact(int(canonical["user_id"]), "telegram_username", username, is_primary=False)
        return canonical

    async def get_entity_by_contact(
        self,
        contact_type: str,
        contact_value: str,
    ) -> User | None:
        row = await self.get_user_by_contact(contact_type, contact_value)
        return user_from_row(row) if row is not None else None

    async def register_telegram_entity(
        self,
        telegram_id: int,
        username: str | None,
        name_or_nick: str,
    ) -> User:
        row = await self.register_telegram_user(telegram_id, username, name_or_nick)
        return user_from_row(row)
