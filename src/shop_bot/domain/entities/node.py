from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from shop_bot.domain.errors import DomainValidationError, InvalidStateTransition


class NodeStatus(StrEnum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class NodeTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeTaskOperation(StrEnum):
    PROVISION_CLIENT = "provision_client"
    REVOKE_CLIENT = "revoke_client"
    SYNC_STATUS = "sync_status"


PROVISION_SUCCESS_STATUSES = frozenset({"provisioned", "already_exists"})
REVOKE_SUCCESS_STATUSES = frozenset({"revoked", "not_found_treated_as_success"})


@dataclass(slots=True)
class Node:
    id: int | None
    node_key: str
    display_name: str
    api_base_url: str
    status: NodeStatus = NodeStatus.UNKNOWN
    is_enabled: bool = True
    selection_weight: int = 100
    last_seen_at: datetime | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        self.status = NodeStatus(self.status)
        if self.selection_weight <= 0:
            raise DomainValidationError("Node selection weight must be positive")

    def is_available(self) -> bool:
        return self.is_enabled and self.status in {NodeStatus.ONLINE, NodeStatus.DEGRADED}

    def mark_online(self, now: datetime) -> None:
        self.status = NodeStatus.ONLINE
        self.last_seen_at = now
        self.last_error = None

    def mark_offline(self, now: datetime, error: str) -> None:
        self.status = NodeStatus.OFFLINE
        self.last_error = error


@dataclass(slots=True)
class NodeTask:
    id: int | None
    task_uuid: UUID
    node_id: int
    operation: NodeTaskOperation
    status: NodeTaskStatus
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    vpn_configuration_id: int | None = None
    subscription_id: int | None = None
    vpn_generation: int | None = None
    response_payload: dict[str, Any] | None = None
    remote_client_ref: str | None = None
    attempts: int = 0
    max_attempts: int = 5
    next_retry_at: datetime | None = None
    last_error: str | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    lease_token: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    journal_retired_at: datetime | None = None

    def __post_init__(self) -> None:
        self.operation = NodeTaskOperation(self.operation)
        self.status = NodeTaskStatus(self.status)
        if self.lease_token is not None and not isinstance(self.lease_token, UUID):
            self.lease_token = UUID(str(self.lease_token))
        if self.max_attempts <= 0 or self.attempts < 0:
            raise DomainValidationError("Invalid node task attempt limits")
        if self.vpn_generation is not None and self.vpn_generation <= 0:
            raise DomainValidationError("Node task VPN generation must be positive")

    def can_dispatch(self, now: datetime) -> bool:
        return self.status is NodeTaskStatus.PENDING and (self.next_retry_at is None or self.next_retry_at <= now)

    def start_attempt(
        self,
        now: datetime,
        *,
        lease_expires_at: datetime,
        lease_token: UUID,
    ) -> int:
        if not self.can_dispatch(now):
            raise InvalidStateTransition("Node task is not ready for dispatch")
        if lease_expires_at <= now:
            raise DomainValidationError("Node task lease must expire after claim time")
        self.status = NodeTaskStatus.IN_PROGRESS
        self.attempts += 1
        self.claimed_at = now
        self.lease_expires_at = lease_expires_at
        self.lease_token = lease_token
        self.last_error = None
        self.updated_at = now
        return self.attempts

    def owns_active_lease(self, token: UUID, now: datetime, attempt_no: int) -> bool:
        return (
            self.status is NodeTaskStatus.IN_PROGRESS
            and self.lease_token == token
            and self.attempts == attempt_no
            and self.lease_expires_at is not None
            and now < self.lease_expires_at
        )

    def lease_is_expired(self, now: datetime) -> bool:
        return (
            self.status is NodeTaskStatus.IN_PROGRESS
            and self.lease_expires_at is not None
            and self.lease_expires_at <= now
        )

    def recover_expired_lease(self, *, now: datetime, error: str, base_delay_seconds: int) -> None:
        if not self.lease_is_expired(now):
            raise InvalidStateTransition("Node task lease is not expired")
        self.retry(error=error, response=None, now=now, base_delay_seconds=base_delay_seconds)

    def clear_lease(self) -> None:
        self.claimed_at = None
        self.lease_expires_at = None
        self.lease_token = None

    def accepts_remote_status(self, remote_status: str) -> bool:
        if self.operation is NodeTaskOperation.PROVISION_CLIENT:
            return remote_status in PROVISION_SUCCESS_STATUSES
        if self.operation is NodeTaskOperation.REVOKE_CLIENT:
            return remote_status in REVOKE_SUCCESS_STATUSES
        return False

    def complete(self, *, response: dict[str, Any], now: datetime) -> None:
        if self.status is not NodeTaskStatus.IN_PROGRESS:
            raise InvalidStateTransition("Only in-progress node task can complete")
        self.status = NodeTaskStatus.SUCCEEDED
        self.response_payload = response
        self.remote_client_ref = response.get("remote_client_ref")
        self.completed_at = now
        self.updated_at = now
        self.last_error = None
        self.clear_lease()

    def retry(self, *, error: str, response: dict[str, Any] | None, now: datetime, base_delay_seconds: int) -> None:
        if self.attempts >= self.max_attempts:
            self.fail(error=error, response=response, now=now)
            return
        delay_seconds = min(base_delay_seconds * (2 ** max(self.attempts - 1, 0)), 300)
        self.status = NodeTaskStatus.PENDING
        self.next_retry_at = now + timedelta(seconds=delay_seconds)
        self.last_error = error
        self.response_payload = response
        self.updated_at = now
        self.clear_lease()

    def fail(self, *, error: str, response: dict[str, Any] | None, now: datetime) -> None:
        self.status = NodeTaskStatus.FAILED
        self.last_error = error
        self.response_payload = response
        self.completed_at = now
        self.updated_at = now
        self.clear_lease()
