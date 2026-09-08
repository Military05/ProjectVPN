from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self

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

Row = Mapping[str, Any]


class UserRepository(Protocol):
    async def get_user_by_contact(self, contact_type: str, contact_value: str) -> Row | None: ...

    async def get_entity_by_contact(self, contact_type: str, contact_value: str) -> User | None: ...

    async def register_telegram_entity(
        self,
        telegram_id: int,
        username: str | None,
        name_or_nick: str,
    ) -> User: ...


class AdminRepository(Protocol):
    async def get_tariff_entity(self, tariff_id: int) -> Tariff | None: ...

    async def list_tariffs(self, *, include_disabled: bool = True) -> list[Row]: ...

    async def create_tariff(
        self,
        tariff_name: str,
        price_minor: int,
        currency: str,
        period_days: int,
        description: str | None,
        is_enabled: bool = True,
    ) -> Row: ...


class SubscriptionRepository(Protocol):
    async def get_current_access_for_user(self, user_id: int, now: datetime) -> Row | None: ...

    async def get_entity(
        self,
        subscription_id: int,
        *,
        for_update: bool = False,
    ) -> Subscription | None: ...

    async def get_active_entity_for_user(
        self,
        user_id: int,
        *,
        for_update: bool = False,
    ) -> Subscription | None: ...

    async def get_last_period_entity(
        self,
        subscription_id: int,
        *,
        for_update: bool = False,
    ) -> SubscriptionPeriod | None: ...

    async def get_current_period_entity(
        self,
        subscription_id: int,
        now: datetime,
    ) -> SubscriptionPeriod | None: ...

    async def add_entity(self, subscription: Subscription) -> Subscription: ...

    async def save_entity(self, subscription: Subscription) -> None: ...

    async def add_period_entity(self, period: SubscriptionPeriod) -> SubscriptionPeriod: ...

    async def get_period_by_payment_order_id(
        self, payment_order_id: int, *, for_update: bool = False
    ) -> SubscriptionPeriod | None: ...

    async def save_period_entity(self, period: SubscriptionPeriod) -> None: ...

    async def has_funded_period_after(self, subscription_id: int, now: datetime) -> bool: ...

    async def list_expired_active_subscription_ids(self, now: datetime) -> list[int]: ...

    async def list_due_subscriptions_for_provision(self, now: datetime) -> list[int]: ...

    async def list_subscriptions(self, limit: int = 100, offset: int = 0) -> list[Row]: ...


class PaymentRepository(Protocol):
    async def get_order_entity(
        self,
        order_id: int,
        *,
        for_update: bool = False,
    ) -> PaymentOrder | None: ...

    async def get_order_entity_by_idempotency(self, idempotency_key: str) -> PaymentOrder | None: ...

    async def get_order(self, order_id: int, *, for_update: bool = False) -> Row | None: ...

    async def list_orders(self, limit: int = 100, offset: int = 0) -> list[Row]: ...

    async def add_order_entity(self, order: PaymentOrder) -> PaymentOrder: ...

    async def save_order_entity(self, order: PaymentOrder) -> None: ...

    async def get_attempt_entity(
        self, payment_attempt_id: int, *, for_update: bool = False
    ) -> PaymentAttempt | None: ...

    async def get_latest_attempt_entity(self, payment_order_id: int) -> PaymentAttempt | None: ...

    async def get_latest_attempt(self, payment_order_id: int) -> Row | None: ...

    async def get_attempt_entity_by_provider_payment_id(
        self,
        provider: str,
        provider_payment_id: str,
    ) -> PaymentAttempt | None: ...

    async def add_attempt_entity(self, attempt: PaymentAttempt) -> PaymentAttempt: ...

    async def save_attempt_entity(self, attempt: PaymentAttempt) -> None: ...

    async def get_event_entity(
        self,
        payment_event_id: int,
        *,
        for_update: bool = False,
    ) -> PaymentEvent | None: ...

    async def create_payment_event(
        self,
        *,
        payment_order_id: int | None,
        payment_attempt_id: int | None,
        provider: str,
        event_type: str,
        event_key: str,
        status: str,
        payload: dict[str, Any],
        occurred_at: datetime,
        provider_payment_id: str | None = None,
        reported_payment_order_id: int | None = None,
        payment_status: str | None = None,
        amount_minor: int | None = None,
        currency: str | None = None,
    ) -> Row: ...

    async def save_event_entity(self, event: PaymentEvent) -> None: ...

    async def list_received_payment_event_ids(self, limit: int = 100) -> list[int]: ...

    async def save_webhook_inbox(
        self,
        *,
        provider: str,
        event_key: str,
        headers: dict[str, Any],
        payload: dict[str, Any],
        status: str,
    ) -> tuple[bool, Row]: ...

    async def mark_webhook_inbox_status(
        self,
        webhook_inbox_id: int,
        status: str,
        *,
        processed_at: datetime | None = None,
        error_message: str | None = None,
    ) -> None: ...

    async def create_provider_transaction(
        self,
        *,
        payment_order_id: int,
        payment_attempt_id: int | None,
        provider: str,
        transaction_type: str,
        provider_transaction_key: str,
        transaction_status: str,
        amount_minor: int,
        currency: str,
        payload: dict[str, Any],
    ) -> None: ...

    async def sum_provider_transactions(
        self, *, payment_order_id: int, provider: str, transaction_type: str, transaction_status: str
    ) -> int: ...

    async def create_outbox_event(
        self,
        *,
        event_name: str,
        aggregate_type: str,
        aggregate_id: int,
        payload: dict[str, Any],
        status: str = "pending",
    ) -> int: ...

    async def list_pending_outbox_events(self, now: datetime, limit: int = 100) -> list[Row]: ...

    async def mark_outbox_published(self, outbox_event_id: int, published_at: datetime) -> None: ...

    async def reschedule_outbox_event(self, outbox_event_id: int, last_error: str, available_at: datetime) -> None: ...


class ServerRepository(Protocol):
    async def list_servers(self) -> list[Row]: ...

    async def create_server(
        self,
        server_name: str,
        host: str,
        is_enabled: bool = True,
    ) -> Row: ...

    async def list_server_endpoints(self) -> list[Row]: ...

    async def create_server_endpoint(self, **payload: Any) -> Row: ...

    async def get_first_enabled_endpoint(self) -> Row | None: ...


class VpnRepository(Protocol):
    async def get_active_configuration_for_user(self, user_id: int, now: datetime) -> Row | None: ...

    async def get_entity(
        self, vpn_configuration_id: int, *, for_update: bool = False
    ) -> VpnConfiguration | None: ...

    async def get_active_entity_for_subscription(
        self,
        subscription_id: int,
    ) -> VpnConfiguration | None: ...

    async def get_latest_entity_for_subscription(
        self,
        subscription_id: int,
    ) -> VpnConfiguration | None: ...

    async def get_configuration_with_endpoint(self, vpn_configuration_id: int) -> Row | None: ...

    async def add_entity(self, configuration: VpnConfiguration) -> VpnConfiguration: ...

    async def save_entity(self, configuration: VpnConfiguration) -> None: ...

    async def get_panel_task_entity(
        self, panel_task_id: int, *, for_update: bool = False
    ) -> PanelProvisionTask | None: ...

    async def get_panel_task_for_configuration(
        self, vpn_configuration_id: int
    ) -> PanelProvisionTask | None: ...

    async def add_panel_task_entity(self, task: PanelProvisionTask) -> PanelProvisionTask: ...

    async def save_panel_task_entity(self, task: PanelProvisionTask) -> None: ...

    async def list_due_panel_task_ids(self, now: datetime, limit: int = 100) -> list[int]: ...

    async def list_stale_panel_task_entities(
        self, now: datetime, limit: int = 100
    ) -> list[PanelProvisionTask]: ...

    async def get_panel_revoke_task_entity(
        self, panel_revoke_task_id: int, *, for_update: bool = False
    ) -> PanelRevokeTask | None: ...

    async def get_panel_revoke_task_for_generation(
        self, vpn_configuration_id: int, vpn_generation: int
    ) -> PanelRevokeTask | None: ...

    async def add_panel_revoke_task_entity(self, task: PanelRevokeTask) -> PanelRevokeTask: ...

    async def save_panel_revoke_task_entity(self, task: PanelRevokeTask) -> None: ...

    async def list_due_panel_revoke_task_ids(self, now: datetime, limit: int = 100) -> list[int]: ...

    async def list_stale_panel_revoke_task_entities(
        self, now: datetime, limit: int = 100
    ) -> list[PanelRevokeTask]: ...

    async def list_active_configuration_ids_due_for_revoke(self, now: datetime) -> list[int]: ...

    async def list_configurations(self, limit: int = 100, offset: int = 0) -> list[Row]: ...


class NodeRepository(Protocol):
    async def get_node_entity(self, node_id: int, *, for_update: bool = False) -> Node | None: ...

    async def get_node(self, node_id: int, *, for_update: bool = False) -> Row | None: ...

    async def get_node_by_key(self, node_key: str) -> Row | None: ...

    async def create_node(
        self,
        *,
        node_key: str,
        display_name: str,
        api_base_url: str,
        is_enabled: bool = True,
        selection_weight: int = 100,
        status: str = "unknown",
    ) -> Row: ...

    async def list_nodes(self, limit: int = 100, offset: int = 0) -> list[Row]: ...

    async def list_enabled_nodes(self) -> list[Row]: ...

    async def save_node_entity(self, node: Node) -> None: ...

    async def create_credential(
        self,
        *,
        node_id: int,
        key_id: str,
        shared_secret: str,
        is_active: bool = True,
        expires_at: datetime | None = None,
    ) -> Row: ...

    async def get_active_credential(self, node_id: int) -> Row | None: ...

    async def get_task_entity(
        self,
        node_task_id: int,
        *,
        for_update: bool = False,
    ) -> NodeTask | None: ...

    async def add_task_entity(self, task: NodeTask) -> NodeTask: ...

    async def save_task_entity(self, task: NodeTask) -> None: ...

    async def get_vpn_task_for_generation(
        self, vpn_configuration_id: int, operation: str, vpn_generation: int
    ) -> NodeTask | None: ...

    async def list_tasks(self, limit: int = 100, offset: int = 0) -> list[Row]: ...

    async def list_due_tasks(self, now: datetime, limit: int = 100) -> list[int]: ...

    async def list_stale_task_entities(self, now: datetime, limit: int = 100) -> list[NodeTask]: ...

    async def create_task_attempt(
        self,
        *,
        node_task_id: int,
        attempt_no: int,
        request_payload: dict[str, Any],
        started_at: datetime,
    ) -> Row: ...

    async def finish_task_attempt(
        self,
        node_task_attempt_id: int,
        *,
        status: str,
        finished_at: datetime,
        response_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None: ...

    async def fail_started_task_attempt(
        self,
        *,
        node_task_id: int,
        attempt_no: int,
        finished_at: datetime,
        error_message: str,
    ) -> bool: ...

    async def mark_node_unreachable(
        self,
        node_id: int,
        *,
        error_message: str,
        seen_at: datetime,
    ) -> None: ...

    async def touch_node_success(self, node_id: int, *, seen_at: datetime) -> None: ...

    async def upsert_node_status(
        self,
        *,
        node_id: int,
        health_status: str,
        agent_version: str | None,
        capabilities: dict[str, Any],
        metrics: dict[str, Any],
        status_payload: dict[str, Any],
        inbounds: list[dict[str, Any]],
        last_seen_at: datetime,
        last_error: str | None = None,
    ) -> None: ...


class UnitOfWork(Protocol):
    users: UserRepository
    admin: AdminRepository
    subscriptions: SubscriptionRepository
    payments: PaymentRepository
    servers: ServerRepository
    vpn: VpnRepository
    nodes: NodeRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
