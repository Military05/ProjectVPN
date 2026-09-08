from __future__ import annotations

from typing import Any
from collections.abc import Mapping


async def sync_expired_subscriptions(container: Any) -> Mapping[str, int]:
    return await container.applications.sync_expired_subscriptions.execute()
