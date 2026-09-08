from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from shop_bot.domain.errors import DomainValidationError, InvalidStateTransition


class VpnConfigurationStatus(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    FAILED = "failed"
    REVOKING = "revoking"
    REVOKE_FAILED = "revoke_failed"
    REVOKED = "revoked"
    EXPIRED = "expired"
    DISABLED = "disabled"


class VpnDesiredState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(slots=True)
class VpnConfiguration:
    id: int | None
    subscription_id: int
    server_endpoint_id: int
    client_uuid: UUID
    display_name: str
    status: VpnConfigurationStatus
    remote_client_ref: str | None = None
    created_at: datetime | None = None
    revoked_at: datetime | None = None
    desired_state: VpnDesiredState = VpnDesiredState.ACTIVE
    generation: int = 1

    def __post_init__(self) -> None:
        self.status = VpnConfigurationStatus(self.status)
        self.desired_state = VpnDesiredState(self.desired_state)
        if self.generation <= 0:
            raise DomainValidationError("VPN configuration generation must be positive")

    def activate(self, remote_client_ref: str | None = None) -> None:
        if self.status is not VpnConfigurationStatus.PROVISIONING:
            raise InvalidStateTransition("Only provisioning VPN configuration can be activated")
        if self.desired_state is not VpnDesiredState.ACTIVE:
            raise InvalidStateTransition("VPN configuration is no longer desired to be active")
        self.status = VpnConfigurationStatus.ACTIVE
        self.remote_client_ref = remote_client_ref or self.remote_client_ref
        self.revoked_at = None

    def begin_revoke(self) -> None:
        if self.status is not VpnConfigurationStatus.ACTIVE:
            raise InvalidStateTransition("Only active VPN configuration can begin revoke")
        self.desired_state = VpnDesiredState.REVOKED
        self.generation += 1
        self.status = VpnConfigurationStatus.REVOKING

    def retry_revoke(self) -> None:
        if self.status is not VpnConfigurationStatus.REVOKE_FAILED:
            raise InvalidStateTransition("Only failed revoke can be retried")
        self.desired_state = VpnDesiredState.REVOKED
        self.generation += 1
        self.status = VpnConfigurationStatus.REVOKING
        self.revoked_at = None

    def cancel_provisioning_for_revoke(self) -> None:
        if self.status is not VpnConfigurationStatus.PROVISIONING:
            raise InvalidStateTransition("Only provisioning VPN can be cancelled for revoke")
        self.status = VpnConfigurationStatus.FAILED
        self.desired_state = VpnDesiredState.REVOKED
        self.generation += 1

    def request_cleanup_from_failed(self) -> None:
        if self.status is not VpnConfigurationStatus.FAILED:
            raise InvalidStateTransition("Only failed VPN can request cleanup")
        self.desired_state = VpnDesiredState.REVOKED
        self.generation += 1

    def revoke(self, now: datetime) -> None:
        if self.status is not VpnConfigurationStatus.REVOKING:
            raise InvalidStateTransition("Only revoking VPN configuration can be revoked")
        if self.desired_state is not VpnDesiredState.REVOKED:
            raise InvalidStateTransition("VPN configuration is not desired to be revoked")
        self.status = VpnConfigurationStatus.REVOKED
        self.revoked_at = now

    def expire(self, remote_client_ref: str | None = None) -> None:
        """Legacy compatibility transition; new remote finalizers must not use it."""
        self.status = VpnConfigurationStatus.EXPIRED
        self.desired_state = VpnDesiredState.REVOKED
        self.remote_client_ref = remote_client_ref or self.remote_client_ref

    def fail_provisioning(self) -> None:
        if self.status is not VpnConfigurationStatus.PROVISIONING:
            raise InvalidStateTransition("Only provisioning VPN configuration can fail provisioning")
        self.status = VpnConfigurationStatus.FAILED

    def fail_revoke(self) -> None:
        if self.status is not VpnConfigurationStatus.REVOKING:
            raise InvalidStateTransition("Only revoking VPN configuration can fail revoke")
        self.status = VpnConfigurationStatus.REVOKE_FAILED

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            VpnConfigurationStatus.REVOKED,
            VpnConfigurationStatus.EXPIRED,
            VpnConfigurationStatus.DISABLED,
        }
