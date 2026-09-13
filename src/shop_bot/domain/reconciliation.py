from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReconciliationAnomalyKind(StrEnum):
    EXPIRE_SUBSCRIPTION = "expire_subscription"
    REVOKE_VPN = "revoke_vpn"
    CLEANUP_VPN = "cleanup_vpn"
    PROVISION_SUBSCRIPTION = "provision_subscription"
    REPAIR_PROVISION = "repair_provision"
    REPAIR_REVOKE = "repair_revoke"


@dataclass(frozen=True, slots=True)
class ReconciliationAnomaly:
    kind_order: int
    kind: ReconciliationAnomalyKind
    entity_id: int

