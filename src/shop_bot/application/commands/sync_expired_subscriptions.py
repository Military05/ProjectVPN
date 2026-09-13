from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shop_bot.application.commands.reconcile_subscriptions import reconcile_subscriptions


async def sync_expired_subscriptions(container: Any) -> Mapping[str, int | str]:
    """Compatibility wrapper for queued jobs created before CHANGE-05."""

    return await reconcile_subscriptions(container)
