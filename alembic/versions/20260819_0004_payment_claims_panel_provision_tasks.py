"""payment creation claims and durable panel provisioning tasks

Revision ID: 20260819_0004
Revises: 20260819_0003
Create Date: 2026-08-19 12:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260819_0004"
down_revision = "20260819_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_attempts", sa.Column("creation_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payment_attempts", sa.Column("creation_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payment_attempts", sa.Column("creation_lease_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(
        "uq_payment_attempts_one_creation_claim_per_order",
        "payment_attempts",
        ["payment_order_id"],
        unique=True,
        postgresql_where=sa.text("status = 'created'"),
    )

    op.create_table(
        "panel_provision_tasks",
        sa.Column("panel_provision_task_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "vpn_configuration_id",
            sa.BigInteger(),
            sa.ForeignKey("vpn_configurations.vpn_configuration_id", onupdate="RESTRICT", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            sa.BigInteger(),
            sa.ForeignKey("subscriptions.subscription_id", onupdate="RESTRICT", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("compensation_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("vpn_configuration_id", name="uq_panel_provision_tasks_vpn_configuration_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_panel_provision_tasks_idempotency_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'cancelled')",
            name="panel_provision_tasks_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="panel_provision_tasks_attempts_non_negative"),
        sa.CheckConstraint("max_attempts > 0", name="panel_provision_tasks_max_attempts_positive"),
    )
    op.create_index(
        "idx_panel_provision_tasks_status_next_retry_at",
        "panel_provision_tasks",
        ["status", "next_retry_at"],
    )
    op.create_index(
        "idx_panel_provision_tasks_status_lease_expires_at",
        "panel_provision_tasks",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_panel_provision_tasks_status_lease_expires_at", table_name="panel_provision_tasks")
    op.drop_index("idx_panel_provision_tasks_status_next_retry_at", table_name="panel_provision_tasks")
    op.drop_table("panel_provision_tasks")
    op.drop_index("uq_payment_attempts_one_creation_claim_per_order", table_name="payment_attempts")
    op.drop_column("payment_attempts", "creation_lease_token")
    op.drop_column("payment_attempts", "creation_lease_expires_at")
    op.drop_column("payment_attempts", "creation_claimed_at")
