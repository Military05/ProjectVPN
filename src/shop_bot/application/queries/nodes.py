from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shop_bot.bootstrap.container import ServiceContainer


async def list_nodes(container: ServiceContainer) -> list[Mapping[str, Any]]:
    async with container.uow() as uow:
        return await uow.nodes.list_nodes()


async def list_node_tasks(container: ServiceContainer) -> list[Mapping[str, Any]]:
    async with container.uow() as uow:
        return await uow.nodes.list_tasks()
