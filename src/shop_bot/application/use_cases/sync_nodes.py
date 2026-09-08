from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from shop_bot.application.ports import NodeGateway, UnitOfWorkFactory


@dataclass(slots=True)
class SyncNodeStatus:
    uow_factory: UnitOfWorkFactory
    node_gateway: NodeGateway
    clock: Callable[[], datetime]
    concurrency: int = 10
    batch_size: int = 100
    probe_lease_seconds: int = 30
    stale_after_seconds: int = 180

    async def execute(self, *, node_id: int | None = None) -> dict[str, Any]:
        now = self.clock()
        async with self.uow_factory() as uow:
            if node_id is not None:
                node = await uow.nodes.get_node(node_id)
                nodes = [node] if node is not None else []
            elif hasattr(uow.nodes, "claim_due_probe_targets"):
                nodes = await uow.nodes.claim_due_probe_targets(
                    now, now - timedelta(seconds=self.stale_after_seconds), self.batch_size, self.probe_lease_seconds
                )
                await uow.commit()
            else:
                nodes = await uow.nodes.list_enabled_nodes()
            await uow.commit()

        queue: asyncio.Queue[Any] = asyncio.Queue()
        for node in nodes:
            queue.put_nowait(node)
        synced = offline = 0
        counter_lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal synced, offline
            while True:
                try:
                    claimed = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    node = claimed.node if hasattr(claimed, "node") else claimed
                    current_id = int(claimed.node_id if hasattr(claimed, "node_id") else node["node_id"])
                    async with self.uow_factory() as uow:
                        credential = await uow.nodes.get_active_credential(current_id)
                    if credential is None:
                        continue
                    try:
                        snapshot = await self._snapshot(node=node, credential=credential)
                        completed = self.clock()
                        health = dict(snapshot.get("health") or {})
                        capabilities = dict(snapshot.get("capabilities") or {})
                        status_payload = dict(snapshot.get("status") or {})
                        metrics = dict(snapshot.get("metrics") or {
                            "active_clients": status_payload.get("active_clients"),
                            "max_clients": status_payload.get("max_clients"),
                            "load": status_payload.get("load", {}),
                            "traffic": status_payload.get("traffic", {}),
                        })
                        async with self.uow_factory() as uow:
                            if hasattr(claimed, "lease_token"):
                                accepted = await uow.nodes.finalize_probe(
                                    node_id=current_id, lease_token=claimed.lease_token,
                                    health_status=str(status_payload.get("status") or "online").lower(),
                                    completed_at=completed, last_seen_at=completed, last_error=None,
                                    capabilities=capabilities, metrics=metrics, status_payload=status_payload,
                                    inbounds=list(snapshot.get("inbounds") or status_payload.get("inbounds") or []),
                                    agent_version=str(health.get("agent_version")) if health.get("agent_version") else None,
                                )
                            else:
                                await uow.nodes.upsert_node_status(
                                    node_id=current_id,
                                    health_status=str(status_payload.get("status") or "online").lower(),
                                    agent_version=str(health.get("agent_version")) if health.get("agent_version") else None,
                                    capabilities=capabilities, metrics=metrics, status_payload=status_payload,
                                    inbounds=list(status_payload.get("inbounds") or []), last_seen_at=completed,
                                    last_checked_at=completed,
                                )
                                accepted = True
                        if accepted:
                            async with counter_lock:
                                synced += 1
                    except Exception as exc:
                        completed = self.clock()
                        async with self.uow_factory() as uow:
                            if hasattr(claimed, "lease_token"):
                                await uow.nodes.finalize_probe(
                                    node_id=current_id, lease_token=claimed.lease_token, health_status="offline",
                                    completed_at=completed, last_seen_at=None, last_error=str(exc),
                                    capabilities={}, metrics={}, status_payload={}, inbounds=[], agent_version=None,
                                )
                            else:
                                await uow.nodes.mark_node_unreachable(current_id, error_message=str(exc), seen_at=completed)
                        async with counter_lock:
                            offline += 1
                finally:
                    queue.task_done()

        await asyncio.gather(*(worker() for _ in range(max(1, self.concurrency))))
        return {"status": "ok", "synced": synced, "offline": offline}

    async def _snapshot(self, *, node: Any, credential: Any) -> dict[str, Any]:
        if hasattr(self.node_gateway, "get_snapshot"):
            return await self.node_gateway.get_snapshot(node=node, credential=credential)
        health, capabilities, status = await asyncio.gather(
            self.node_gateway.get_health(node=node, credential=credential),
            self.node_gateway.get_capabilities(node=node, credential=credential),
            self.node_gateway.get_status(node=node, credential=credential),
        )
        return {"health": health, "capabilities": capabilities, "status": status}
