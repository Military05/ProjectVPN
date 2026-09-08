from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def dispatch_node_task(container: Any, *, node_task_id: int) -> Mapping[str, Any]:
    return await container.applications.dispatch_node_task.execute(node_task_id=node_task_id)


async def dispatch_due_node_tasks(
    container: Any, *, limit: int = 100
) -> Mapping[str, Any]:
    return await container.applications.dispatch_node_task.dispatch_due(limit=limit)
