from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from shop_bot.apps.api.deps import get_container
from shop_bot.apps.api.pagination import set_pagination_headers
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
async def get_nodes(
    response: Response,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    container: ServiceContainer = Depends(get_container),
) -> list[NodeResponse]:
    rows = await container.queries.nodes.list_nodes(limit=limit + 1, offset=offset)
    set_pagination_headers(response, limit=limit, offset=offset, has_more=len(rows) > limit)
    return [NodeResponse.model_validate(row) for row in rows[:limit]]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_node(
    payload: CreateNodeRequest,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, object]:
    result = await container.applications.node_admin.create_node(
        node_key=payload.node_key,
        display_name=payload.display_name,
        api_base_url=str(payload.api_base_url),
        is_enabled=payload.is_enabled,
        selection_weight=payload.selection_weight,
        key_id=payload.key_id,
        shared_secret=payload.shared_secret,
    )
    return {
        "node": NodeResponse.model_validate(result["node"]).model_dump(mode="json"),
        "credential": NodeCredentialResponse.model_validate(result["credential"]).model_dump(mode="json"),
    }


@router.post("/{node_id}/sync", response_model=NodeSyncResponse)
async def sync_node(
    node_id: int,
    container: ServiceContainer = Depends(get_container),
) -> NodeSyncResponse:
    result = await container.applications.node_admin.sync_node(node_id)
    return NodeSyncResponse.model_validate(result)


@router.get("/tasks", response_model=list[NodeTaskResponse])
async def get_tasks(
    response: Response,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    container: ServiceContainer = Depends(get_container),
) -> list[NodeTaskResponse]:
    rows = await container.queries.nodes.list_tasks(limit=limit + 1, offset=offset)
    set_pagination_headers(response, limit=limit, offset=offset, has_more=len(rows) > limit)
    return [NodeTaskResponse.model_validate(row) for row in rows[:limit]]


@router.post("/tasks/{node_task_id}/dispatch")
async def dispatch_task(
    node_task_id: int,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, int | str]:
    return await container.applications.node_admin.dispatch_task(node_task_id)
