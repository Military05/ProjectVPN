from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def sync_node_status(container: Any, *, node_id: int | None = None) -> Mapping[str, Any]:
    return await container.applications.sync_node_status.execute(node_id=node_id)
