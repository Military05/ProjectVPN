from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from shop_bot.domain.entities import (
    Node,
    NodeTask,
    PanelProvisionTask,
    PanelRevokeTask,
    PaymentAttempt,
    PaymentEvent,
    PaymentOrder,
    Subscription,
    SubscriptionPeriod,
    Tariff,
    User,
    VpnConfiguration,
)
from shop_bot.domain.value_objects.money import Money


def user_from_row(row: Mapping[str, Any]) -> User:
    return User(
        id=int(row["user_id"]),
        name_or_nick=str(row["name_or_nick"]),
        created_at=row.get("created_at"),
    )


def tariff_from_row(row: Mapping[str, Any]) -> Tariff:
    return Tariff(
        id=int(row["tariff_id"]),
        name=str(row["tariff_name"]),
        price=Money(int(row["price_minor"]), str(row["currency"])),
        period_days=int(row["period_days"]),
        description=row.get("description"),
        is_enabled=bool(row["is_enabled"]),
    )


def subscription_from_row(row: Mapping[str, Any]) -> Subscription:
    return Subscription(
        id=int(row["subscription_id"]),
        user_id=int(row["user_id"]),
        tariff_id=int(row["tariff_id"]),
        status=str(row["status"]),
        created_at=row["created_at"],
        ended_at=row.get("ended_at"),
    )


def subscription_period_from_row(row: Mapping[str, Any]) -> SubscriptionPeriod:
    return SubscriptionPeriod(
        id=int(row["subscription_period_id"]),
        subscription_id=int(row["subscription_id"]),
        starts_at=row["starts_at"],
        expires_at=row["expires_at"],
        is_paid=bool(row["is_paid"]),
        created_at=row["created_at"],
        payment_order_id=int(row["payment_order_id"]) if row.get("payment_order_id") is not None else None,
    )


def payment_order_from_row(row: Mapping[str, Any]) -> PaymentOrder:
    return PaymentOrder(
        id=int(row["payment_order_id"]),
        user_id=int(row["user_id"]),
        tariff_id=int(row["tariff_id"]),
        provider=str(row["provider"]),
        status=str(row["status"]),
        amount=Money(int(row["amount_minor"]), str(row["currency"])),
        requested_period_days=int(row["requested_period_days"]),
        idempotency_key=str(row["idempotency_key"]),
        metadata=dict(row.get("metadata") or {}),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        paid_at=row.get("paid_at"),
    )


def payment_attempt_from_row(row: Mapping[str, Any]) -> PaymentAttempt:
    return PaymentAttempt(
        id=int(row["payment_attempt_id"]),
        payment_order_id=int(row["payment_order_id"]),
        provider=str(row["provider"]),
        provider_payment_id=row.get("provider_payment_id"),
        status=str(row["status"]),
        payment_url=row.get("payment_url"),
        payload=dict(row.get("payload") or {}),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        creation_claimed_at=row.get("creation_claimed_at"),
        creation_lease_expires_at=row.get("creation_lease_expires_at"),
        creation_lease_token=row.get("creation_lease_token"),
    )


def payment_event_from_row(row: Mapping[str, Any]) -> PaymentEvent:
    return PaymentEvent(
        id=int(row["payment_event_id"]),
        payment_order_id=int(row["payment_order_id"]) if row.get("payment_order_id") is not None else None,
        payment_attempt_id=int(row["payment_attempt_id"]) if row.get("payment_attempt_id") is not None else None,
        provider=str(row["provider"]),
        event_type=str(row["event_type"]),
        event_key=str(row["event_key"]),
        status=str(row["status"]),
        payload=dict(row.get("payload") or {}),
        occurred_at=row["occurred_at"],
        provider_payment_id=row.get("provider_payment_id"),
        reported_payment_order_id=(
            int(row["reported_payment_order_id"]) if row.get("reported_payment_order_id") is not None else None
        ),
        payment_status=row.get("payment_status"),
        amount_minor=int(row["amount_minor"]) if row.get("amount_minor") is not None else None,
        currency=row.get("currency"),
        received_at=row.get("received_at"),
        processed_at=row.get("processed_at"),
        error_message=row.get("error_message"),
    )


def vpn_configuration_from_row(row: Mapping[str, Any]) -> VpnConfiguration:
    client_uuid = row["client_uuid"]
    return VpnConfiguration(
        id=int(row["vpn_configuration_id"]),
        subscription_id=int(row["subscription_id"]),
        server_endpoint_id=int(row["server_endpoint_id"]),
        client_uuid=client_uuid if isinstance(client_uuid, UUID) else UUID(str(client_uuid)),
        display_name=str(row["display_name"]),
        status=str(row["status"]),
        remote_client_ref=row.get("remote_client_ref"),
        created_at=row.get("created_at"),
        revoked_at=row.get("revoked_at"),
        desired_state=str(row["desired_state"]),
        generation=int(row["generation"]),
    )


def panel_provision_task_from_row(row: Mapping[str, Any]) -> PanelProvisionTask:
    return PanelProvisionTask(
        id=int(row["panel_provision_task_id"]),
        vpn_configuration_id=int(row["vpn_configuration_id"]),
        subscription_id=int(row["subscription_id"]),
        status=str(row["status"]),
        idempotency_key=str(row["idempotency_key"]),
        payload=dict(row.get("payload") or {}),
        vpn_generation=int(row["vpn_generation"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        next_retry_at=row.get("next_retry_at"),
        last_error=row.get("last_error"),
        claimed_at=row.get("claimed_at"),
        lease_expires_at=row.get("lease_expires_at"),
        lease_token=row.get("lease_token"),
        compensation_required=bool(row.get("compensation_required")),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        completed_at=row.get("completed_at"),
        journal_retired_at=row.get("journal_retired_at"),
    )


def panel_revoke_task_from_row(row: Mapping[str, Any]) -> PanelRevokeTask:
    task_uuid = row["task_uuid"]
    return PanelRevokeTask(
        id=int(row["panel_revoke_task_id"]),
        task_uuid=task_uuid if isinstance(task_uuid, UUID) else UUID(str(task_uuid)),
        vpn_configuration_id=int(row["vpn_configuration_id"]),
        subscription_id=int(row["subscription_id"]),
        vpn_generation=int(row["vpn_generation"]),
        status=str(row["status"]),
        idempotency_key=str(row["idempotency_key"]),
        payload=dict(row.get("payload") or {}),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        next_retry_at=row.get("next_retry_at"),
        last_error=row.get("last_error"),
        claimed_at=row.get("claimed_at"),
        lease_expires_at=row.get("lease_expires_at"),
        lease_token=row.get("lease_token"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        completed_at=row.get("completed_at"),
    )


def node_from_row(row: Mapping[str, Any]) -> Node:
    return Node(
        id=int(row["node_id"]),
        node_key=str(row["node_key"]),
        display_name=str(row["display_name"]),
        api_base_url=str(row["api_base_url"]),
        status=str(row["status"]),
        is_enabled=bool(row["is_enabled"]),
        selection_weight=int(row["selection_weight"]),
        last_seen_at=row.get("last_seen_at"),
        last_error=row.get("last_error"),
    )


def node_task_from_row(row: Mapping[str, Any]) -> NodeTask:
    task_uuid = row["task_uuid"]
    return NodeTask(
        id=int(row["node_task_id"]),
        task_uuid=task_uuid if isinstance(task_uuid, UUID) else UUID(str(task_uuid)),
        node_id=int(row["node_id"]),
        vpn_configuration_id=int(row["vpn_configuration_id"]) if row.get("vpn_configuration_id") is not None else None,
        subscription_id=int(row["subscription_id"]) if row.get("subscription_id") is not None else None,
        vpn_generation=int(row["vpn_generation"]) if row.get("vpn_generation") is not None else None,
        operation=str(row["operation"]),
        status=str(row["status"]),
        idempotency_key=str(row["idempotency_key"]),
        payload=dict(row.get("payload") or {}),
        response_payload=dict(row["response_payload"]) if row.get("response_payload") is not None else None,
        remote_client_ref=row.get("remote_client_ref"),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        next_retry_at=row.get("next_retry_at"),
        last_error=row.get("last_error"),
        claimed_at=row.get("claimed_at"),
        lease_expires_at=row.get("lease_expires_at"),
        lease_token=row.get("lease_token"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        completed_at=row.get("completed_at"),
    )
