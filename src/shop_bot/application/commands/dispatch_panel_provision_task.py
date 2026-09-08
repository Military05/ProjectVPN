from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def dispatch_panel_provision_task(container: Any, *, panel_task_id: int) -> Mapping[str, Any]:
    return await container.applications.dispatch_panel_provision_task.execute(panel_task_id=panel_task_id)


async def dispatch_due_panel_provision_tasks(
    container: Any, *, limit: int = 100
) -> Mapping[str, Any]:
    return await container.applications.dispatch_panel_provision_task.dispatch_due(limit=limit)


async def recover_stale_panel_provision_tasks(
    container: Any, *, limit: int = 100
) -> Mapping[str, Any]:
    return await container.applications.dispatch_panel_provision_task.recover_stale(limit=limit)
