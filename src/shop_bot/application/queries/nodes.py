from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def list_nodes(
    container: Any,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[Mapping[str, Any]]:
    """Compatibility query controller for existing route imports."""
    return await container.queries.nodes.list_nodes(limit=limit, offset=offset)


async def list_node_tasks(
    container: Any,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[Mapping[str, Any]]:
    """Compatibility query controller for existing route imports."""
    return await container.queries.nodes.list_tasks(limit=limit, offset=offset)
