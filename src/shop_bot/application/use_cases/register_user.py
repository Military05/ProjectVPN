from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shop_bot.application.ports import UnitOfWorkFactory


@dataclass(slots=True)
class RegisterBotUser:
    uow_factory: UnitOfWorkFactory

    async def execute(
        self,
        *,
        telegram_id: int,
        username: str | None,
        name_or_nick: str,
    ) -> dict[str, Any]:
        async with self.uow_factory() as uow:
            user = await uow.users.register_telegram_entity(telegram_id, username, name_or_nick)
        return {"user_id": user.id, "name_or_nick": user.name_or_nick}
