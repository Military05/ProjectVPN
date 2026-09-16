from enum import StrEnum


class AuditEventName(StrEnum):
    SUBSCRIPTION_ACTIVATED = "subscription_activated"
    SUBSCRIPTION_ENDED = "subscription_ended"
    PAYMENT_MANUALLY_SETTLED = "payment_manually_settled"
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_EXPIRED = "payment_expired"
    PAYMENT_CANCELLED = "payment_cancelled"
    REFUND_ENTITLEMENT_RECONCILIATION_REQUIRED = (
        "refund_entitlement_reconciliation_required"
    )
    VPN_CONFIGURATION_CREATED = "vpn_configuration_created"
    VPN_CONFIGURATION_ACTIVATED = "vpn_configuration_activated"
    VPN_CONFIGURATION_REVOKED = "vpn_configuration_revoked"


class AuditAggregateType(StrEnum):
    SUBSCRIPTION = "subscription"
    PAYMENT_ORDER = "payment_order"
    VPN_CONFIGURATION = "vpn_configuration"
