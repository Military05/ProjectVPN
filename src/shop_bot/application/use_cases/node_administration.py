from __future__ import annotations

from shop_bot.application.job_names import JobName
import secrets
from dataclasses import dataclass
from typing import Any, Callable

from shop_bot.application.ports import JobQueue, UnitOfWorkFactory
from shop_bot.application.use_cases.sync_nodes import SyncNodeStatus
from shop_bot.core.exceptions import ConflictError, NotFoundError


def _generate_shared_secret() -> str:
    return secrets.token_urlsafe(32)


@dataclass(slots=True)
class NodeAdministration:
    uow_factory: UnitOfWorkFactory
    job_queue: JobQueue
    sync_status: SyncNodeStatus
    secret_factory: Callable[[], str] = _generate_shared_secret

    async def list_nodes(self) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            return [dict(row) for row in await uow.nodes.list_nodes()]

    async def list_tasks(self) -> list[dict[str, Any]]:
        async with self.uow_factory() as uow:
            return [dict(row) for row in await uow.nodes.list_tasks()]

    async def create_node(
        self,
        *,
        node_key: str,
        display_name: str,
        api_base_url: str,
        is_enabled: bool,
        selection_weight: int,
        key_id: str,
        shared_secret: str | None,
    ) -> dict[str, Any]:
        secret = shared_secret or self.secret_factory()
        async with self.uow_factory() as uow:
            node = await uow.nodes.create_node(
                node_key=node_key,
                display_name=display_name,
                api_base_url=api_base_url,
                is_enabled=is_enabled,
                selection_weight=selection_weight,
            )
            credential = await uow.nodes.create_credential(
                node_id=int(node["node_id"]),
                key_id=key_id,
                shared_secret=secret,
                is_active=True,
            )
        return {"node": dict(node), "credential": dict(credential)}

    async def sync_node(self, node_id: int) -> dict[str, Any]:
        result = await self.sync_status.execute(node_id=node_id)
        if result.get("synced", 0) == 0 and result.get("offline", 0) == 0:
            raise NotFoundError("Node not found")
        async with self.uow_factory() as uow:
            node = await uow.nodes.get_node(node_id)
        if node is None:
            raise NotFoundError("Node not found")
        return {
            "node_id": node_id,
            "health_status": str(node["status"]),
            "last_seen_at": node.get("last_seen_at"),
        }

    async def dispatch_task(self, node_task_id: int) -> dict[str, int | str]:
        async with self.uow_factory() as uow:
            if await uow.nodes.get_task_entity(node_task_id) is None:
                raise NotFoundError("Node task not found")
        await self.job_queue.enqueue(JobName.DISPATCH_NODE_TASK, node_task_id)
        return {"node_task_id": node_task_id, "status": "queued"}
