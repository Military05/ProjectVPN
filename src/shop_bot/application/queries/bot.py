from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def list_bot_tariffs(container: Any) -> list[Mapping[str, Any]]:
    """Compatibility query controller for existing API and Telegram handlers."""
    return await container.queries.bot.list_tariffs()


async def get_user_dashboard(container: Any, *, telegram_id: int) -> Mapping[str, Any]:
    """Compatibility query controller for existing API and Telegram handlers."""
    return await container.queries.bot.get_dashboard(telegram_id=telegram_id)
