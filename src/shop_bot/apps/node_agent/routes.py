from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from shop_bot.infrastructure.nodes.auth import NodeAuthError, verify_signed_request
from shop_bot.schemas.nodes import (
    AgentCapabilitiesResponse,
    AgentHealthResponse,
    AgentOperationResponse,
    AgentProvisionRequest,
    AgentRevokeRequest,
    AgentStatusResponse,
)

router = APIRouter(prefix="/agent", tags=["node-agent"])


async def require_node_auth(request: Request) -> None:
    body = await request.body()
    try:
        verify_signed_request(
            method=request.method,
            path=request.url.path,
            body=body,
            headers=request.headers,
            expected_node_id=request.app.state.settings.node_agent_node_key,
            expected_key_id=request.app.state.settings.node_agent_key_id,
            shared_secret=request.app.state.settings.node_agent_shared_secret,
            tolerance_seconds=request.app.state.settings.node_timestamp_tolerance_seconds,
            nonce_store=request.app.state.nonce_store,
        )
    except NodeAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.get("/health", response_model=AgentHealthResponse, dependencies=[Depends(require_node_auth)])
async def health(request: Request) -> AgentHealthResponse:
    payload = await request.app.state.runtime.health()
    return AgentHealthResponse.model_validate(payload)


@router.get("/capabilities", response_model=AgentCapabilitiesResponse, dependencies=[Depends(require_node_auth)])
async def capabilities(request: Request) -> AgentCapabilitiesResponse:
    payload = await request.app.state.runtime.capabilities()
    return AgentCapabilitiesResponse.model_validate(payload)


@router.get("/status", response_model=AgentStatusResponse, dependencies=[Depends(require_node_auth)])
async def status_route(request: Request) -> AgentStatusResponse:
    payload = await request.app.state.runtime.status()
    return AgentStatusResponse.model_validate(payload)


@router.post("/clients/provision", response_model=AgentOperationResponse, dependencies=[Depends(require_node_auth)])
async def provision(
    payload: AgentProvisionRequest,
    request: Request,
) -> AgentOperationResponse:
    response = await request.app.state.runtime.provision_client(payload.model_dump(mode="json"))
    return AgentOperationResponse.model_validate(response)


@router.post("/clients/revoke", response_model=AgentOperationResponse, dependencies=[Depends(require_node_auth)])
async def revoke(
    payload: AgentRevokeRequest,
    request: Request,
) -> AgentOperationResponse:
    response = await request.app.state.runtime.revoke_client(payload.model_dump(mode="json"))
    return AgentOperationResponse.model_validate(response)
