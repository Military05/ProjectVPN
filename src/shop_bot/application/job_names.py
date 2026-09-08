from enum import StrEnum


class JobName(StrEnum):
    PROCESS_PAYMENT_EVENT = "process_payment_event"
    PROVISION_SUBSCRIPTION = "provision_subscription"
    REVOKE_VPN_CONFIGURATION = "revoke_vpn_configuration"
    DISPATCH_NODE_TASK = "dispatch_node_task"
    DISPATCH_PANEL_PROVISION_TASK = "dispatch_panel_provision_task"
    DISPATCH_PANEL_REVOKE_TASK = "dispatch_panel_revoke_task"
    PUBLISH_OUTBOX = "publish_outbox"
