"""multinode architecture

Revision ID: 20260425_0002
Revises: 20260419_0001
Create Date: 2026-04-25 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260425_0002"
down_revision = "20260419_0001"
branch_labels = None
depends_on = None


def _drop_vpn_status_check_constraints() -> None:
    op.execute(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN
                SELECT c.conname
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE t.relname = 'vpn_configurations'
                  AND n.nspname = current_schema()
                  AND c.contype = 'c'
                  AND (
                      c.conname = 'ck_vpn_configurations_vpn_configurations_status'
                      OR c.conname LIKE 'ck_vpn_configurations%status%'
                  )
            LOOP
                EXECUTE format(
                    'ALTER TABLE vpn_configurations DROP CONSTRAINT IF EXISTS %I',
                    r.conname
                );
            END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column("node_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("node_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("api_base_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("selection_weight", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("node_key", name="uq_nodes_node_key"),
        sa.CheckConstraint("btrim(node_key) <> ''", name="ck_nodes_nodes_node_key_not_blank"),
        sa.CheckConstraint("btrim(display_name) <> ''", name="ck_nodes_nodes_display_name_not_blank"),
        sa.CheckConstraint("btrim(api_base_url) <> ''", name="ck_nodes_nodes_api_base_url_not_blank"),
        sa.CheckConstraint("status IN ('unknown', 'online', 'offline', 'degraded')", name="ck_nodes_nodes_status"),
        sa.CheckConstraint("selection_weight > 0", name="ck_nodes_nodes_selection_weight_positive"),
    )
    op.create_index("idx_nodes_status", "nodes", ["status"])
    op.create_index("idx_nodes_is_enabled", "nodes", ["is_enabled"])
    op.create_index("idx_nodes_last_seen_at", "nodes", ["last_seen_at"])

    op.create_table(
        "node_credentials",
        sa.Column("node_credential_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("node_id", sa.BigInteger(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("shared_secret", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.node_id"], onupdate="RESTRICT", ondelete="CASCADE"),
        sa.UniqueConstraint("node_id", "key_id", name="uq_node_credentials_node_key_id"),
        sa.CheckConstraint("btrim(key_id) <> ''", name="ck_node_credentials_node_credentials_key_id_not_blank"),
        sa.CheckConstraint(
            "btrim(shared_secret) <> ''",
            name="ck_node_credentials_node_credentials_shared_secret_not_blank",
        ),
    )
    op.create_index(
        "uq_node_credentials_one_active_per_node",
        "node_credentials",
        ["node_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index("idx_node_credentials_node_id", "node_credentials", ["node_id"])

    op.create_table(
        "node_status",
        sa.Column("node_id", sa.BigInteger(), primary_key=True),
        sa.Column("health_status", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("inbounds", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.node_id"], onupdate="RESTRICT", ondelete="CASCADE"),
        sa.CheckConstraint(
            "health_status IN ('unknown', 'online', 'offline', 'degraded')",
            name="ck_node_status_node_status_health_status",
        ),
    )
    op.create_index("idx_node_status_health_status", "node_status", ["health_status"])
    op.create_index("idx_node_status_last_seen_at", "node_status", ["last_seen_at"])

    op.add_column("server_endpoints", sa.Column("node_id", sa.BigInteger(), nullable=True))
    op.add_column("server_endpoints", sa.Column("local_inbound_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_server_endpoints_node_id_nodes",
        "server_endpoints",
        "nodes",
        ["node_id"],
        ["node_id"],
        onupdate="RESTRICT",
        ondelete="SET NULL",
    )
    op.create_index("idx_server_endpoints_node_id", "server_endpoints", ["node_id"])
    op.create_index("idx_server_endpoints_local_inbound_id", "server_endpoints", ["local_inbound_id"])

    op.add_column("vpn_configurations", sa.Column("remote_client_ref", sa.Text(), nullable=True))
    _drop_vpn_status_check_constraints()
    op.create_check_constraint(
        "ck_vpn_configurations_vpn_configurations_status",
        "vpn_configurations",
        "status IN ('provisioning', 'active', 'failed', 'revoking', 'revoke_failed', 'revoked', 'expired', 'disabled')",
    )

    op.create_table(
        "node_tasks",
        sa.Column("node_task_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("task_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.BigInteger(), nullable=False),
        sa.Column("vpn_configuration_id", sa.BigInteger(), nullable=True),
        sa.Column("subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("remote_client_ref", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.node_id"], onupdate="RESTRICT", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["vpn_configuration_id"],
            ["vpn_configurations.vpn_configuration_id"],
            onupdate="RESTRICT",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.subscription_id"],
            onupdate="RESTRICT",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("task_uuid", name="uq_node_tasks_task_uuid"),
        sa.UniqueConstraint("idempotency_key", name="uq_node_tasks_idempotency_key"),
        sa.CheckConstraint(
            "operation IN ('provision_client', 'revoke_client', 'sync_status')",
            name="ck_node_tasks_node_tasks_operation",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'cancelled')",
            name="ck_node_tasks_node_tasks_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_node_tasks_node_tasks_attempts_non_negative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_node_tasks_node_tasks_max_attempts_positive"),
    )
    op.create_index("idx_node_tasks_node_id", "node_tasks", ["node_id"])
    op.create_index("idx_node_tasks_status_next_retry_at", "node_tasks", ["status", "next_retry_at"])
    op.create_index("idx_node_tasks_vpn_configuration_id", "node_tasks", ["vpn_configuration_id"])

    op.create_table(
        "node_task_attempts",
        sa.Column("node_task_attempt_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("node_task_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["node_task_id"], ["node_tasks.node_task_id"], onupdate="RESTRICT", ondelete="CASCADE"),
        sa.UniqueConstraint("node_task_id", "attempt_no", name="uq_node_task_attempts_node_task_attempt_no"),
        sa.CheckConstraint("attempt_no > 0", name="ck_node_task_attempts_node_task_attempts_attempt_no_positive"),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'failed')",
            name="ck_node_task_attempts_node_task_attempts_status",
        ),
    )
    op.create_index("idx_node_task_attempts_node_task_id", "node_task_attempts", ["node_task_id"])


def downgrade() -> None:
    op.drop_index("idx_node_task_attempts_node_task_id", table_name="node_task_attempts")
    op.drop_table("node_task_attempts")

    op.drop_index("idx_node_tasks_vpn_configuration_id", table_name="node_tasks")
    op.drop_index("idx_node_tasks_status_next_retry_at", table_name="node_tasks")
    op.drop_index("idx_node_tasks_node_id", table_name="node_tasks")
    op.drop_table("node_tasks")

    _drop_vpn_status_check_constraints()
    op.create_check_constraint(
        "ck_vpn_configurations_vpn_configurations_status",
        "vpn_configurations",
        "status IN ('active', 'revoked', 'expired', 'disabled')",
    )
    op.drop_column("vpn_configurations", "remote_client_ref")

    op.drop_index("idx_server_endpoints_local_inbound_id", table_name="server_endpoints")
    op.drop_index("idx_server_endpoints_node_id", table_name="server_endpoints")
    op.drop_constraint("fk_server_endpoints_node_id_nodes", "server_endpoints", type_="foreignkey")
    op.drop_column("server_endpoints", "local_inbound_id")
    op.drop_column("server_endpoints", "node_id")

    op.drop_index("idx_node_status_last_seen_at", table_name="node_status")
    op.drop_index("idx_node_status_health_status", table_name="node_status")
    op.drop_table("node_status")

    op.drop_index("idx_node_credentials_node_id", table_name="node_credentials")
    op.drop_index("uq_node_credentials_one_active_per_node", table_name="node_credentials")
    op.drop_table("node_credentials")

    op.drop_index("idx_nodes_last_seen_at", table_name="nodes")
    op.drop_index("idx_nodes_is_enabled", table_name="nodes")
    op.drop_index("idx_nodes_status", table_name="nodes")
    op.drop_table("nodes")