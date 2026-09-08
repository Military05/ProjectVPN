from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from shop_bot.domain.errors import DomainValidationError, InvalidStateTransition


class PanelProvisionTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PanelRevokeTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class PanelProvisionTask:
    id: int | None
    vpn_configuration_id: int
    subscription_id: int
    status: PanelProvisionTaskStatus
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    vpn_generation: int = 1
    attempts: int = 0
    max_attempts: int = 5
    next_retry_at: datetime | None = None
    last_error: str | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    lease_token: UUID | None = None
    compensation_required: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        self.status = PanelProvisionTaskStatus(self.status)
        if self.lease_token is not None and not isinstance(self.lease_token, UUID):
            self.lease_token = UUID(str(self.lease_token))
        if self.attempts < 0 or self.max_attempts <= 0:
            raise DomainValidationError("Invalid panel task attempt limits")
        if self.vpn_generation <= 0:
            raise DomainValidationError("Panel task VPN generation must be positive")
        if not self.idempotency_key.strip():
            raise DomainValidationError("Panel task idempotency key cannot be blank")

    def can_dispatch(self, now: datetime) -> bool:
        return self.status is PanelProvisionTaskStatus.PENDING and (
            self.next_retry_at is None or self.next_retry_at <= now
        )

    def start_attempt(self, *, now: datetime, lease_expires_at: datetime, lease_token: UUID) -> int:
        if not self.can_dispatch(now):
            raise InvalidStateTransition("Panel task is not ready for dispatch")
        if lease_expires_at <= now:
            raise DomainValidationError("Panel task lease must expire after claim time")
        self.status = PanelProvisionTaskStatus.IN_PROGRESS
        self.attempts += 1
        self.claimed_at = now
        self.lease_expires_at = lease_expires_at
        self.lease_token = lease_token
        self.last_error = None
        self.updated_at = now
        return self.attempts

    def owns_active_lease(self, *, token: UUID, attempt_no: int, now: datetime) -> bool:
        return (
            self.status is PanelProvisionTaskStatus.IN_PROGRESS
            and self.lease_token == token
            and self.attempts == attempt_no
            and self.lease_expires_at is not None
            and now < self.lease_expires_at
        )

    def owns_lease(self, *, token: UUID, attempt_no: int) -> bool:
        return (
            self.status is PanelProvisionTaskStatus.IN_PROGRESS
            and self.lease_token == token
            and self.attempts == attempt_no
        )

    def lease_is_expired(self, now: datetime) -> bool:
        return (
            self.status is PanelProvisionTaskStatus.IN_PROGRESS
            and self.lease_expires_at is not None
            and self.lease_expires_at <= now
        )

    def clear_lease(self) -> None:
        self.claimed_at = None
        self.lease_expires_at = None
        self.lease_token = None

    def complete(self, *, now: datetime) -> None:
        if self.status is not PanelProvisionTaskStatus.IN_PROGRESS:
            raise InvalidStateTransition("Only in-progress panel task can complete")
        self.status = PanelProvisionTaskStatus.SUCCEEDED
        self.completed_at = now
        self.updated_at = now
        self.last_error = None
        self.compensation_required = False
        self.clear_lease()

    def cancel(self, *, now: datetime) -> None:
        self.status = PanelProvisionTaskStatus.CANCELLED
        self.completed_at = now
        self.updated_at = now
        self.last_error = None
        self.compensation_required = False
        self.clear_lease()

    def retry(self, *, now: datetime, error: str, base_delay_seconds: int) -> None:
        if self.attempts >= self.max_attempts:
            self.fail(now=now, error=error)
            return
        delay = min(base_delay_seconds * (2 ** max(self.attempts - 1, 0)), 300)
        self.status = PanelProvisionTaskStatus.PENDING
        self.next_retry_at = now + timedelta(seconds=delay)
        self.last_error = error
        self.updated_at = now
        self.clear_lease()

    def fail(self, *, now: datetime, error: str) -> None:
        self.status = PanelProvisionTaskStatus.FAILED
        self.last_error = error
        self.completed_at = now
        self.updated_at = now
        self.clear_lease()

    def recover_expired_lease(self, *, now: datetime, error: str) -> None:
        if not self.lease_is_expired(now):
            raise InvalidStateTransition("Panel task lease is not expired")
        self.status = PanelProvisionTaskStatus.PENDING
        self.next_retry_at = now
        self.last_error = error
        self.compensation_required = True
        self.updated_at = now
        self.clear_lease()

    def mark_compensation_required(self, *, now: datetime, error: str) -> None:
        if self.status is not PanelProvisionTaskStatus.IN_PROGRESS:
            raise InvalidStateTransition("Only in-progress panel task can require owned compensation")
        self.compensation_required = True
        self.last_error = error
        self.updated_at = now

    def require_compensation(self, *, now: datetime, error: str) -> None:
        self.status = PanelProvisionTaskStatus.PENDING
        self.next_retry_at = now
        self.last_error = error
        self.compensation_required = True
        self.updated_at = now
        self.clear_lease()

    def compensation_succeeded(self, *, now: datetime, terminal_local_state: bool) -> None:
        self.compensation_required = False
        self.last_error = None
        self.updated_at = now
        self.clear_lease()
        if terminal_local_state:
            self.status = PanelProvisionTaskStatus.CANCELLED
            self.completed_at = now
            return
        if self.attempts >= self.max_attempts:
            self.status = PanelProvisionTaskStatus.FAILED
            self.completed_at = now
            return
        self.status = PanelProvisionTaskStatus.PENDING
        self.next_retry_at = now
        self.completed_at = None


@dataclass(slots=True)
class PanelRevokeTask:
    id: int | None
    task_uuid: UUID
    vpn_configuration_id: int
    subscription_id: int
    vpn_generation: int
    status: PanelRevokeTaskStatus
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
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

    def __post_init__(self) -> None:
        self.status = PanelRevokeTaskStatus(self.status)
        if not isinstance(self.task_uuid, UUID):
            self.task_uuid = UUID(str(self.task_uuid))
        if self.lease_token is not None and not isinstance(self.lease_token, UUID):
            self.lease_token = UUID(str(self.lease_token))
        if self.vpn_generation <= 0:
            raise DomainValidationError("Panel revoke task VPN generation must be positive")
        if self.attempts < 0 or self.max_attempts <= 0:
            raise DomainValidationError("Invalid panel revoke task attempt limits")
        if not self.idempotency_key.strip():
            raise DomainValidationError("Panel revoke task idempotency key cannot be blank")

    def can_dispatch(self, now: datetime) -> bool:
        return self.status is PanelRevokeTaskStatus.PENDING and (
            self.next_retry_at is None or self.next_retry_at <= now
        )

    def start_attempt(self, *, now: datetime, lease_expires_at: datetime, lease_token: UUID) -> int:
        if not self.can_dispatch(now):
            raise InvalidStateTransition("Panel revoke task is not ready for dispatch")
        if lease_expires_at <= now:
            raise DomainValidationError("Panel revoke task lease must expire after claim time")
        self.status = PanelRevokeTaskStatus.IN_PROGRESS
        self.attempts += 1
        self.claimed_at = now
        self.lease_expires_at = lease_expires_at
        self.lease_token = lease_token
        self.last_error = None
        self.updated_at = now
        return self.attempts

    def owns_active_lease(self, *, token: UUID, attempt_no: int, now: datetime) -> bool:
        return (
            self.status is PanelRevokeTaskStatus.IN_PROGRESS
            and self.lease_token == token
            and self.attempts == attempt_no
            and self.lease_expires_at is not None
            and now < self.lease_expires_at
        )

    def owns_lease(self, *, token: UUID, attempt_no: int) -> bool:
        return (
            self.status is PanelRevokeTaskStatus.IN_PROGRESS
            and self.lease_token == token
            and self.attempts == attempt_no
        )

    def lease_is_expired(self, now: datetime) -> bool:
        return (
            self.status is PanelRevokeTaskStatus.IN_PROGRESS
            and self.lease_expires_at is not None
            and self.lease_expires_at <= now
        )

    def clear_lease(self) -> None:
        self.claimed_at = None
        self.lease_expires_at = None
        self.lease_token = None

    def complete(self, *, now: datetime) -> None:
        if self.status is not PanelRevokeTaskStatus.IN_PROGRESS:
            raise InvalidStateTransition("Only in-progress panel revoke task can complete")
        self.status = PanelRevokeTaskStatus.SUCCEEDED
        self.completed_at = now
        self.updated_at = now
        self.last_error = None
        self.clear_lease()

    def retry(self, *, now: datetime, error: str, base_delay_seconds: int) -> None:
        if self.attempts >= self.max_attempts:
            self.fail(now=now, error=error)
            return
        delay = min(base_delay_seconds * (2 ** max(self.attempts - 1, 0)), 300)
        self.status = PanelRevokeTaskStatus.PENDING
        self.next_retry_at = now + timedelta(seconds=delay)
        self.last_error = error
        self.updated_at = now
        self.clear_lease()

    def fail(self, *, now: datetime, error: str) -> None:
        self.status = PanelRevokeTaskStatus.FAILED
        self.last_error = error
        self.completed_at = now
        self.updated_at = now
        self.clear_lease()

    def recover_expired_lease(self, *, now: datetime, error: str) -> None:
        if not self.lease_is_expired(now):
            raise InvalidStateTransition("Panel revoke task lease is not expired")
        self.status = PanelRevokeTaskStatus.PENDING
        self.next_retry_at = now
        self.last_error = error
        self.updated_at = now
        self.clear_lease()
