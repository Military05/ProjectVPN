from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def reconcile_subscriptions(container: Any) -> Mapping[str, int | str]:
    return await container.applications.reconcile_subscriptions.execute()
