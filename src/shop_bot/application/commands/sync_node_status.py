from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow
from shop_bot.infrastructure.nodes.client import NodeApiClient


async def sync_node_status(
    container: ServiceContainer,
    *,
    node_id: int | None = None,
) -> Mapping[str, Any]:
    now = utcnow()
    async with container.uow() as uow:
        if node_id is not None:
            node = await uow.nodes.get_node(node_id)
            nodes = [node] if node is not None else []
        else:
            nodes = await uow.nodes.list_enabled_nodes()

    client = NodeApiClient(container.settings)
    synced = 0
    offline = 0
    for node in nodes:
        if node is None:
            continue
        async with container.uow() as uow:
            credential = await uow.nodes.get_active_credential(int(node["node_id"]))
        if credential is None:
            continue
        try:
            health = await client.get_health(node=node, credential=credential)
            capabilities = await client.get_capabilities(node=node, credential=credential)
            status = await client.get_status(node=node, credential=credential)
            async with container.uow() as uow:
                await uow.nodes.upsert_node_status(
                    node_id=int(node["node_id"]),
                    health_status=str(status.get("status") or health.get("status") or "online"),
                    agent_version=str(health.get("agent_version")) if health.get("agent_version") else None,
                    capabilities=dict(capabilities),
                    metrics={
                        "active_clients": status.get("active_clients", 0),
                        "max_clients": status.get("max_clients", 0),
                        "load": status.get("load", {}),
                        "traffic": status.get("traffic", {}),
                    },
                    status_payload=dict(status),
                    inbounds=list(status.get("inbounds") or []),
                    last_seen_at=now,
                    last_error=None,
                )
            synced += 1
        except Exception as exc:  # pragma: no cover - network/path dependent
            async with container.uow() as uow:
                await uow.nodes.mark_node_unreachable(int(node["node_id"]), error_message=str(exc), seen_at=now)
            offline += 1
    return {"status": "ok", "synced": synced, "offline": offline}
