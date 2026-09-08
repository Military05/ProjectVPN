from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from shop_bot.domain.entities.node import PROVISION_SUCCESS_STATUSES, REVOKE_SUCCESS_STATUSES
from shop_bot.infrastructure.nodes.auth import (
    NodeAuthError,
    VerifiedNodeRequest,
    verify_signed_request,
)
from shop_bot.infrastructure.nodes.idempotency import JournalClaimKind
from shop_bot.schemas.nodes import (
    AgentCapabilitiesResponse,
    AgentHealthResponse,
    AgentOperationResponse,
    AgentProvisionRequest,
    AgentRevokeRequest,
    AgentStatusResponse,
    AgentSnapshotResponse,
)

router = APIRouter(prefix="/agent", tags=["node-agent"])


class JournalRetireRequest(BaseModel):
    idempotency_key: str


async def require_node_auth(request: Request) -> VerifiedNodeRequest:
    body = await request.body()
    try:
        return verify_signed_request(
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


@router.get("/snapshot", response_model=AgentSnapshotResponse, dependencies=[Depends(require_node_auth)])
async def snapshot(request: Request) -> AgentSnapshotResponse:
    health_payload, capabilities_payload, status_payload = await _snapshot_payload(request)
    return AgentSnapshotResponse(
        health=AgentHealthResponse.model_validate(health_payload),
        capabilities=AgentCapabilitiesResponse.model_validate(capabilities_payload),
        status=AgentStatusResponse.model_validate(status_payload),
        agent_version=str(health_payload.get("agent_version") or ""),
        metrics={"active_clients": status_payload.get("active_clients", 0), "max_clients": status_payload.get("max_clients", 0), "load": status_payload.get("load", {}), "traffic": status_payload.get("traffic", {})},
        inbounds=list(status_payload.get("inbounds") or []),
    )


@router.post("/idempotency/retire", dependencies=[Depends(require_node_auth)])
async def retire_journal(payload: JournalRetireRequest, request: Request) -> dict[str, str]:
    result = await request.app.state.operation_journal.retire(payload.idempotency_key)
    if result == "operation_in_progress":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result)
    return {"status": result}


async def _snapshot_payload(request: Request) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    runtime = request.app.state.runtime
    if hasattr(runtime, "snapshot"):
        payload = await runtime.snapshot()
        return dict(payload["health"]), dict(payload["capabilities"]), dict(payload["status"])
    return await runtime.health(), await runtime.capabilities(), await runtime.status()


@router.post("/clients/provision", response_model=AgentOperationResponse)
async def provision(
    payload: AgentProvisionRequest,
    request: Request,
    verified: VerifiedNodeRequest = Depends(require_node_auth),
) -> AgentOperationResponse:
    return await _execute_operation(
        request=request,
        verified=verified,
        payload=payload.model_dump(mode="json"),
        operation="provision",
    )


@router.post("/clients/revoke", response_model=AgentOperationResponse)
async def revoke(
    payload: AgentRevokeRequest,
    request: Request,
    verified: VerifiedNodeRequest = Depends(require_node_auth),
) -> AgentOperationResponse:
    return await _execute_operation(
        request=request,
        verified=verified,
        payload=payload.model_dump(mode="json"),
        operation="revoke",
    )


async def _execute_operation(
    *,
    request: Request,
    verified: VerifiedNodeRequest,
    payload: dict[str, Any],
    operation: Literal["provision", "revoke"],
) -> AgentOperationResponse:
    if request.app.state.settings.node_agent_runtime_mode == "xui":
        try:
            inbound_id = int(str(payload.get("inbound_id", "")).strip())
            if inbound_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="inbound_id must be a positive integer")
    body_key = str(payload["idempotency_key"])
    if body_key != verified.idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signed idempotency key does not match request body",
        )

    raw_body = await request.body()
    request_hash = sha256(
        request.method.upper().encode("utf-8")
        + b"\n"
        + request.url.path.encode("utf-8")
        + b"\n"
        + raw_body
    ).hexdigest()
    journal = request.app.state.operation_journal
    claim = await journal.claim(
        idempotency_key=verified.idempotency_key,
        request_hash=request_hash,
        operation=operation,
        client_uuid=str(payload["client_uuid"]),
        inbound_id=str(payload["inbound_id"]),
    )
    if claim.kind is JournalClaimKind.CONFLICT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key was already used for a different request",
        )
    if claim.kind is JournalClaimKind.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operation with this idempotency key is already in progress",
        )
    if claim.kind is JournalClaimKind.REPLAY:
        return AgentOperationResponse.model_validate(claim.response or {})
    if claim.owner_token is None:
        raise RuntimeError("Journal owner claim is missing owner token")

    owner_token = claim.owner_token
    try:
        if operation == "provision" and claim.reclaimed:
            cleanup = await request.app.state.runtime.revoke_client(
                {
                    "client_uuid": str(payload["client_uuid"]),
                    "inbound_id": str(payload["inbound_id"]),
                }
            )
            if str(cleanup.get("status")) not in REVOKE_SUCCESS_STATUSES:
                raise RuntimeError("Ambiguous provision cleanup did not converge")

        if operation == "provision":
            raw_response = await request.app.state.runtime.provision_client(payload)
            success_statuses = PROVISION_SUCCESS_STATUSES
        else:
            raw_response = await request.app.state.runtime.revoke_client(payload)
            success_statuses = REVOKE_SUCCESS_STATUSES
        response = AgentOperationResponse.model_validate(raw_response)
        response_payload = response.model_dump(mode="json")

        if response.status not in success_statuses:
            await journal.mark_retryable(
                idempotency_key=verified.idempotency_key,
                owner_token=owner_token,
                error=response.message or response.error_code or f"remote status={response.status}",
            )
            return response

        finalized = await journal.complete(
            idempotency_key=verified.idempotency_key,
            owner_token=owner_token,
            response=response_payload,
        )
        if not finalized:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Operation ownership changed before journal finalization",
            )
        return response
    except HTTPException:
        raise
    except Exception as exc:
        await journal.mark_retryable(
            idempotency_key=verified.idempotency_key,
            owner_token=owner_token,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
