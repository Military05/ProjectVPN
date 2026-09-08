from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def register_bot_user(
    container: Any,
    *,
    telegram_id: int,
    username: str | None,
    name_or_nick: str,
) -> Mapping[str, Any]:
    """Compatibility controller for the object-oriented use case."""
    return await container.applications.register_user.execute(
        telegram_id=telegram_id,
        username=username,
        name_or_nick=name_or_nick,
    )
