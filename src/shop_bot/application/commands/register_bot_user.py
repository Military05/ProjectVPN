from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shop_bot.bootstrap.container import ServiceContainer


async def register_bot_user(
    container: ServiceContainer,
    *,
    telegram_id: int,
    username: str | None,
    name_or_nick: str,
) -> Mapping[str, Any]:
    async with container.uow() as uow:
        return await uow.users.register_telegram_user(telegram_id=telegram_id, username=username, name_or_nick=name_or_nick)
