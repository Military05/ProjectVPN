from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def list_nodes(container: Any) -> list[Mapping[str, Any]]:
    """Compatibility query controller for existing route imports."""
    return await container.queries.nodes.list_nodes()


async def list_node_tasks(container: Any) -> list[Mapping[str, Any]]:
    """Compatibility query controller for existing route imports."""
    return await container.queries.nodes.list_tasks()
