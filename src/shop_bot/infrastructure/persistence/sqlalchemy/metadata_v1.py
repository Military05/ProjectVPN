from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Identity,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

naming_convention = {
    "ix": "ix_%(table_name)s_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=naming_convention)
json_type = JSONB().with_variant(JSON(), "sqlite")

users = Table(
    "users",
    metadata,
    Column("user_id", BigInteger, Identity(always=True), primary_key=True),
    Column("name_or_nick", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("btrim(name_or_nick) <> ''", name="users_name_or_nick_not_blank"),
)
Index("idx_users_created_at", users.c.created_at)
Index("idx_users_name_or_nick", users.c.name_or_nick)

user_contacts = Table(
    "user_contacts",
    metadata,
    Column("user_contact_id", BigInteger, Identity(always=True), primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.user_id", onupdate="RESTRICT", ondelete="RESTRICT"), nullable=False),
    Column("contact_type", Text, nullable=False),
    Column("contact_value", Text, nullable=False),
    Column("is_primary", Boolean, nullable=False, server_default=text("true")),
    UniqueConstraint("contact_type", "contact_value", name="uq_user_contacts_type_value"),
    CheckConstraint("btrim(contact_type) <> ''", name="user_contacts_type_not_blank"),
    CheckConstraint("btrim(contact_value) <> ''", name="user_contacts_value_not_blank"),
)
Index("idx_user_contacts_user_id", user_contacts.c.user_id)
Index("idx_user_contacts_contact_type", user_contacts.c.contact_type)
Index("idx_user_contacts_contact_value", user_contacts.c.contact_value)
Index("idx_user_contacts_user_primary", user_contacts.c.user_id, user_contacts.c.is_primary)

tariffs = Table(
    "tariffs",
    metadata,
    Column("tariff_id", BigInteger, Identity(always=True), primary_key=True),
    Column("tariff_name", Text, nullable=False),
    UniqueConstraint("tariff_name", name="uq_tariffs_tariff_name"),
    CheckConstraint("btrim(tariff_name) <> ''", name="tariffs_tariff_name_not_blank"),
)
Index("idx_tariffs_tariff_name", tariffs.c.tariff_name)

tariff_specs = Table(
    "tariff_specs",
    metadata,
    Column("tariff_id", BigInteger, ForeignKey("tariffs.tariff_id", onupdate="RESTRICT", ondelete="CASCADE"), primary_key=True),
    Column("price_minor", BigInteger, nullable=False),
    Column("currency", Text, nullable=False, server_default=text("'RUB'")),
    Column("period_days", Integer, nullable=False),
    Column("description", Text, nullable=True),
    Column("is_enabled", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("price_minor > 0", name="tariff_specs_price_positive"),
    CheckConstraint("period_days > 0", name="tariff_specs_period_days_positive"),
    CheckConstraint("btrim(currency) <> ''", name="tariff_specs_currency_not_blank"),
)
Index("idx_tariff_specs_is_enabled", tariff_specs.c.is_enabled)
Index("idx_tariff_specs_price_minor", tariff_specs.c.price_minor)

subscriptions = Table(
    "subscriptions",
    metadata,
    Column("subscription_id", BigInteger, Identity(always=True), primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.user_id", onupdate="RESTRICT", ondelete="RESTRICT"), nullable=False),
    Column("tariff_id", BigInteger, ForeignKey("tariffs.tariff_id", onupdate="RESTRICT", ondelete="RESTRICT"), nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    Column("ended_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("status IN ('active', 'ended', 'cancelled', 'paused')", name="subscriptions_status"),
    CheckConstraint("ended_at IS NULL OR ended_at >= created_at", name="subscriptions_ended_at_consistency"),
    CheckConstraint("NOT (status = 'active' AND ended_at IS NOT NULL)", name="subscriptions_active_ended_at"),
)
Index(
    "uq_subscriptions_one_active_per_user",
    subscriptions.c.user_id,
    unique=True,
    postgresql_where=text("status = 'active'"),
)
Index("idx_subscriptions_user_id", subscriptions.c.user_id)
Index("idx_subscriptions_tariff_id", subscriptions.c.tariff_id)
Index("idx_subscriptions_status", subscriptions.c.status)
Index("idx_subscriptions_created_at", subscriptions.c.created_at)
Index("idx_subscriptions_user_status", subscriptions.c.user_id, subscriptions.c.status)

subscription_periods = Table(
    "subscription_periods",
    metadata,
    Column(
        "subscription_period_id",
        BigInteger,
        Identity(always=True),
        primary_key=True,
    ),
    Column(
        "subscription_id",
        BigInteger,
        ForeignKey("subscriptions.subscription_id", onupdate="RESTRICT", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("starts_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("is_paid", Boolean, nullable=False, server_default=text("false")),
    Column(
        "payment_order_id",
        BigInteger,
        ForeignKey("payment_orders.payment_order_id", onupdate="RESTRICT", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("payment_order_id", name="uq_subscription_periods_payment_order_id"),
    CheckConstraint("starts_at < expires_at", name="subscription_periods_time_range"),
    CheckConstraint("created_at <= expires_at", name="subscription_periods_created_at_consistency"),
)
Index("idx_subscription_periods_subscription_id", subscription_periods.c.subscription_id)
Index(
    "idx_subscription_periods_subscription_starts_at",
    subscription_periods.c.subscription_id,
    subscription_periods.c.starts_at,
)
Index(
    "idx_subscription_periods_subscription_expires_at",
    subscription_periods.c.subscription_id,
    subscription_periods.c.expires_at,
)
Index(
    "idx_subscription_periods_paid_time_window",
    subscription_periods.c.subscription_id,
    subscription_periods.c.is_paid,
    subscription_periods.c.starts_at,
    subscription_periods.c.expires_at,
)
Index(
    "idx_subscription_periods_current_access_lookup",
    subscription_periods.c.is_paid,
    subscription_periods.c.starts_at,
    subscription_periods.c.expires_at,
)

servers = Table(
    "servers",
    metadata,
    Column("server_id", BigInteger, Identity(always=True), primary_key=True),
    Column("server_name", Text, nullable=False),
    Column("host", Text, nullable=False),
    Column("is_enabled", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("server_name", name="uq_servers_server_name"),
    UniqueConstraint("host", name="uq_servers_host"),
    CheckConstraint("btrim(server_name) <> ''", name="servers_server_name_not_blank"),
    CheckConstraint("btrim(host) <> ''", name="servers_host_not_blank"),
)
Index("idx_servers_created_at", servers.c.created_at)
Index("idx_servers_is_enabled", servers.c.is_enabled)

server_endpoints = Table(
    "server_endpoints",
    metadata,
    Column("server_endpoint_id", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "server_id",
        BigInteger,
        ForeignKey("servers.server_id", onupdate="RESTRICT", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("protocol", Text, nullable=False),
    Column("port", Integer, nullable=False),
    Column("security", Text, nullable=True),
    Column("sni", Text, nullable=True),
    Column("fingerprint", Text, nullable=True),
    Column("public_key", Text, nullable=True),
    Column("short_id", Text, nullable=True),
    Column("transport_type", Text, nullable=True),
    Column("flow", Text, nullable=True),
    Column("encryption", Text, nullable=True),
    Column("is_enabled", Boolean, nullable=False, server_default=text("true")),
    CheckConstraint("btrim(protocol) <> ''", name="server_endpoints_protocol_not_blank"),
    CheckConstraint("port BETWEEN 1 AND 65535", name="server_endpoints_port_range"),
    UniqueConstraint(
        "server_id",
        "protocol",
        "port",
        "security",
        "sni",
        "public_key",
        "short_id",
        "transport_type",
        "flow",
        "encryption",
        name="uq_server_endpoints_full_tuple",
        postgresql_nulls_not_distinct=True,
    ),
)
Index("idx_server_endpoints_server_id", server_endpoints.c.server_id)
Index("idx_server_endpoints_protocol", server_endpoints.c.protocol)
Index("idx_server_endpoints_port", server_endpoints.c.port)
Index(
    "idx_server_endpoints_server_protocol_port",
    server_endpoints.c.server_id,
    server_endpoints.c.protocol,
    server_endpoints.c.port,
)
Index("idx_server_endpoints_is_enabled", server_endpoints.c.is_enabled)
Index("idx_server_endpoints_transport_type", server_endpoints.c.transport_type)

vpn_configurations = Table(
    "vpn_configurations",
    metadata,
    Column("vpn_configuration_id", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "subscription_id",
        BigInteger,
        ForeignKey("subscriptions.subscription_id", onupdate="RESTRICT", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "server_endpoint_id",
        BigInteger,
        ForeignKey("server_endpoints.server_endpoint_id", onupdate="RESTRICT", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("client_uuid", UUID(as_uuid=True), nullable=False),
    Column("display_name", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("client_uuid", name="uq_vpn_configurations_client_uuid"),
    CheckConstraint("btrim(display_name) <> ''", name="vpn_configurations_display_name_not_blank"),
    CheckConstraint("status IN ('active', 'revoked', 'expired', 'disabled')", name="vpn_configurations_status"),
    CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="vpn_configurations_revoked_at_consistency"),
    CheckConstraint("NOT (status = 'active' AND revoked_at IS NOT NULL)", name="vpn_configurations_active_revoked_at"),
)
Index(
    "uq_vpn_configurations_one_active_per_subscription",
    vpn_configurations.c.subscription_id,
    unique=True,
    postgresql_where=text("status = 'active'"),
)
Index("idx_vpn_configurations_subscription_id", vpn_configurations.c.subscription_id)
Index("idx_vpn_configurations_server_endpoint_id", vpn_configurations.c.server_endpoint_id)
Index("idx_vpn_configurations_status", vpn_configurations.c.status)
Index("idx_vpn_configurations_created_at", vpn_configurations.c.created_at)
Index(
    "idx_vpn_configurations_subscription_status",
    vpn_configurations.c.subscription_id,
    vpn_configurations.c.status,
)

payment_orders = Table(
    "payment_orders",
    metadata,
    Column("payment_order_id", BigInteger, Identity(always=True), primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False),
    Column("tariff_id", BigInteger, ForeignKey("tariffs.tariff_id", ondelete="RESTRICT"), nullable=False),
    Column("provider", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("amount_minor", BigInteger, nullable=False),
    Column("currency", Text, nullable=False),
    Column("requested_period_days", Integer, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("metadata", json_type, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    Column("paid_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("status IN ('pending', 'paid', 'cancelled', 'expired', 'failed')", name="payment_orders_status"),
    CheckConstraint("amount_minor > 0", name="payment_orders_amount_positive"),
    CheckConstraint("requested_period_days > 0", name="payment_orders_requested_period_days_positive"),
    CheckConstraint("btrim(provider) <> ''", name="payment_orders_provider_not_blank"),
    CheckConstraint("btrim(currency) <> ''", name="payment_orders_currency_not_blank"),
)
Index("uq_payment_orders_idempotency_key", payment_orders.c.idempotency_key, unique=True)
Index("idx_payment_orders_user_id", payment_orders.c.user_id)
Index("idx_payment_orders_tariff_id", payment_orders.c.tariff_id)
Index("idx_payment_orders_status", payment_orders.c.status)
Index("idx_payment_orders_provider", payment_orders.c.provider)
Index("idx_payment_orders_created_at", payment_orders.c.created_at)

payment_attempts = Table(
    "payment_attempts",
    metadata,
    Column("payment_attempt_id", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "payment_order_id",
        BigInteger,
        ForeignKey("payment_orders.payment_order_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("provider", Text, nullable=False),
    Column("provider_payment_id", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("payment_url", Text, nullable=True),
    Column("payload", json_type, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    CheckConstraint(
        "status IN ('created', 'pending', 'authorized', 'paid', 'failed', 'expired', 'cancelled')",
        name="payment_attempts_status",
    ),
    CheckConstraint("btrim(provider) <> ''", name="payment_attempts_provider_not_blank"),
)
Index(
    "uq_payment_attempts_provider_payment_id",
    payment_attempts.c.provider,
    payment_attempts.c.provider_payment_id,
    unique=True,
    postgresql_where=text("provider_payment_id IS NOT NULL"),
)
Index("idx_payment_attempts_payment_order_id", payment_attempts.c.payment_order_id)
Index("idx_payment_attempts_provider", payment_attempts.c.provider)
Index("idx_payment_attempts_status", payment_attempts.c.status)

payment_events = Table(
    "payment_events",
    metadata,
    Column("payment_event_id", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "payment_order_id",
        BigInteger,
        ForeignKey("payment_orders.payment_order_id", ondelete="CASCADE"),
        nullable=True,
    ),
    Column(
        "payment_attempt_id",
        BigInteger,
        ForeignKey("payment_attempts.payment_attempt_id", ondelete="CASCADE"),
        nullable=True,
    ),
    Column("provider", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("event_key", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("payload", json_type, nullable=False, server_default=text("'{}'::jsonb")),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    Column("processed_at", DateTime(timezone=True), nullable=True),
    Column("error_message", Text, nullable=True),
    CheckConstraint("status IN ('received', 'processing', 'processed', 'ignored', 'failed')", name="payment_events_status"),
)
Index("uq_payment_events_event_key", payment_events.c.event_key, unique=True)
Index("idx_payment_events_payment_order_id", payment_events.c.payment_order_id)
Index("idx_payment_events_payment_attempt_id", payment_events.c.payment_attempt_id)
Index("idx_payment_events_provider", payment_events.c.provider)
Index("idx_payment_events_status", payment_events.c.status)

provider_transactions = Table(
    "provider_transactions",
    metadata,
    Column("provider_transaction_id", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "payment_order_id",
        BigInteger,
        ForeignKey("payment_orders.payment_order_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "payment_attempt_id",
        BigInteger,
        ForeignKey("payment_attempts.payment_attempt_id", ondelete="CASCADE"),
        nullable=True,
    ),
    Column("provider", Text, nullable=False),
    Column("transaction_type", Text, nullable=False),
    Column("provider_transaction_key", Text, nullable=False),
    Column("transaction_status", Text, nullable=False),
    Column("amount_minor", BigInteger, nullable=False),
    Column("currency", Text, nullable=False),
    Column("payload", json_type, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("amount_minor >= 0", name="provider_transactions_amount_non_negative"),
)
Index(
    "uq_provider_transactions_key",
    provider_transactions.c.provider,
    provider_transactions.c.provider_transaction_key,
    unique=True,
)
Index("idx_provider_transactions_payment_order_id", provider_transactions.c.payment_order_id)
Index("idx_provider_transactions_provider", provider_transactions.c.provider)

webhook_inbox = Table(
    "webhook_inbox",
    metadata,
    Column("webhook_inbox_id", BigInteger, Identity(always=True), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("event_key", Text, nullable=False),
    Column("headers", json_type, nullable=False, server_default=text("'{}'::jsonb")),
    Column("payload", json_type, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    Column("processed_at", DateTime(timezone=True), nullable=True),
    Column("error_message", Text, nullable=True),
    CheckConstraint("status IN ('received', 'processing', 'processed', 'ignored', 'failed')", name="webhook_inbox_status"),
)
Index("uq_webhook_inbox_provider_event", webhook_inbox.c.provider, webhook_inbox.c.event_key, unique=True)
Index("idx_webhook_inbox_status", webhook_inbox.c.status)
Index("idx_webhook_inbox_received_at", webhook_inbox.c.received_at)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("outbox_event_id", BigInteger, Identity(always=True), primary_key=True),
    Column("event_name", Text, nullable=False),
    Column("aggregate_type", Text, nullable=False),
    Column("aggregate_id", BigInteger, nullable=False),
    Column("status", Text, nullable=False),
    Column("payload", json_type, nullable=False, server_default=text("'{}'::jsonb")),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("last_error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("status IN ('pending', 'published', 'failed')", name="outbox_events_status"),
    CheckConstraint("attempts >= 0", name="outbox_events_attempts_non_negative"),
)
Index("idx_outbox_events_status_available_at", outbox_events.c.status, outbox_events.c.available_at)
