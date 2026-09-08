from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def dispatch_panel_revoke_task(container: Any, *, panel_revoke_task_id: int) -> Mapping[str, Any]:
    return await container.applications.dispatch_panel_revoke_task.execute(
        panel_revoke_task_id=panel_revoke_task_id
    )


async def dispatch_due_panel_revoke_tasks(
    container: Any, *, limit: int = 100
) -> Mapping[str, Any]:
    return await container.applications.dispatch_panel_revoke_task.dispatch_due(limit=limit)


async def recover_stale_panel_revoke_tasks(
    container: Any, *, limit: int = 100
) -> Mapping[str, Any]:
    return await container.applications.dispatch_panel_revoke_task.recover_stale(limit=limit)
