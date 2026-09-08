from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class CreateNodeRequest(BaseModel):
    node_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    api_base_url: HttpUrl
    is_enabled: bool = True
    selection_weight: int = Field(default=100, gt=0)
    key_id: str = Field(default="default", min_length=1)
    shared_secret: str | None = Field(default=None, min_length=8)


class NodeCredentialResponse(BaseModel):
    node_credential_id: int
    key_id: str
    shared_secret: str
    is_active: bool
    created_at: datetime


class NodeResponse(BaseModel):
    node_id: int
    node_key: str
    display_name: str
    api_base_url: str
    status: str
    is_enabled: bool
    selection_weight: int
    last_seen_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    health_status: str | None = None
    agent_version: str | None = None


class NodeTaskResponse(BaseModel):
    node_task_id: int
    node_id: int
    operation: str
    status: str
    idempotency_key: str
    vpn_configuration_id: int | None = None
    subscription_id: int | None = None
    attempts: int
    max_attempts: int
    next_retry_at: datetime
    last_error: str | None = None
    completed_at: datetime | None = None


class NodeSyncResponse(BaseModel):
    node_id: int
    health_status: str
    last_seen_at: datetime


class AgentHealthResponse(BaseModel):
    status: Literal["ok"]
    node_id: str
    agent_version: str
    time: datetime


class AgentCapabilitiesResponse(BaseModel):
    node_id: str
    supports: dict[str, bool]
    protocols: list[str]
    transports: list[str]
    security: list[str]


class AgentLoadPayload(BaseModel):
    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_percent: float | None = None


class AgentTrafficPayload(BaseModel):
    rx_bytes: int = 0
    tx_bytes: int = 0


class AgentInboundStatus(BaseModel):
    local_inbound_id: str
    port: int
    protocol: str
    security: str | None = None
    transport_type: str | None = None
    public_host: str
    sni: str | None = None
    fingerprint: str | None = None
    public_key: str | None = None
    short_id: str | None = None
    flow: str | None = None
    encryption: str | None = None


class AgentStatusResponse(BaseModel):
    node_id: str
    status: str
    active_clients: int
    max_clients: int
    load: AgentLoadPayload
    traffic: AgentTrafficPayload
    inbounds: list[AgentInboundStatus]


class AgentSnapshotResponse(BaseModel):
    health: AgentHealthResponse
    capabilities: AgentCapabilitiesResponse
    status: AgentStatusResponse
    agent_version: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    inbounds: list[AgentInboundStatus] = Field(default_factory=list)


class AgentProvisionRequest(BaseModel):
    task_id: str
    idempotency_key: str
    client_uuid: str
    display_name: str
    inbound_id: str
    flow: str | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRevokeRequest(BaseModel):
    task_id: str
    idempotency_key: str
    client_uuid: str
    inbound_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentOperationResponse(BaseModel):
    status: str
    client_uuid: str
    remote_client_ref: str | None = None
    applied_at: datetime | None = None
    error_code: str | None = None
    message: str | None = None
