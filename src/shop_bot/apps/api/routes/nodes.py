from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status

from shop_bot.apps.api.deps import get_container
from shop_bot.application.commands._helpers import enqueue_job
from shop_bot.application.commands.sync_node_status import sync_node_status
from shop_bot.application.queries.nodes import list_node_tasks, list_nodes
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.security import require_admin_token
from shop_bot.schemas.nodes import (
    CreateNodeRequest,
    NodeCredentialResponse,
    NodeResponse,
    NodeSyncResponse,
    NodeTaskResponse,
)

router = APIRouter(prefix="/admin/nodes", tags=["nodes"], dependencies=[Depends(require_admin_token)])


@router.get("", response_model=list[NodeResponse])
async def get_nodes(container: ServiceContainer = Depends(get_container)) -> list[NodeResponse]:
    rows = await list_nodes(container)
    return [NodeResponse.model_validate(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_node(
    payload: CreateNodeRequest,
    container: ServiceContainer = Depends(get_container),
) -> dict:
    shared_secret = payload.shared_secret or secrets.token_urlsafe(32)
    async with container.uow() as uow:
        existing = await uow.nodes.get_node_by_key(payload.node_key)
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Node key already exists")
        node = await uow.nodes.create_node(
            node_key=payload.node_key,
            display_name=payload.display_name,
            api_base_url=str(payload.api_base_url),
            is_enabled=payload.is_enabled,
            selection_weight=payload.selection_weight,
        )
        credential = await uow.nodes.create_credential(
            node_id=int(node["node_id"]),
            key_id=payload.key_id,
            shared_secret=shared_secret,
            is_active=True,
        )
    return {
        "node": NodeResponse.model_validate(node).model_dump(mode="json"),
        "credential": NodeCredentialResponse.model_validate(credential).model_dump(mode="json"),
    }


@router.post("/{node_id}/sync", response_model=NodeSyncResponse)
async def sync_node(
    node_id: int,
    container: ServiceContainer = Depends(get_container),
) -> NodeSyncResponse:
    result = await sync_node_status(container, node_id=node_id)
    if result.get("synced", 0) == 0 and result.get("offline", 0) == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    async with container.uow() as uow:
        node = await uow.nodes.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return NodeSyncResponse(
        node_id=node_id,
        health_status=str(node["status"]),
        last_seen_at=node.get("last_seen_at"),
    )


@router.get("/tasks", response_model=list[NodeTaskResponse])
async def get_tasks(container: ServiceContainer = Depends(get_container)) -> list[NodeTaskResponse]:
    rows = await list_node_tasks(container)
    return [NodeTaskResponse.model_validate(row) for row in rows]


@router.post("/tasks/{node_task_id}/dispatch")
async def dispatch_task(node_task_id: int, container: ServiceContainer = Depends(get_container)) -> dict[str, int | str]:
    async with container.uow() as uow:
        task = await uow.nodes.get_task(node_task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node task not found")
    await enqueue_job(container, "dispatch_node_task", node_task_id)
    return {"node_task_id": node_task_id, "status": "queued"}
