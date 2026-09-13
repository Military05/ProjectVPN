from enum import StrEnum


class VpnRevokeReason(StrEnum):
    EXPIRATION = "expiration"
    FORCE = "force"
    REPLACEMENT_CLEANUP = "replacement_cleanup"
