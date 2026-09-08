from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def recover_stale_node_tasks(
    container: Any, *, limit: int = 100
) -> Mapping[str, Any]:
    return await container.applications.dispatch_node_task.recover_stale(limit=limit)
