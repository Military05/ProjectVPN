"""vpn generation fencing and durable panel revoke tasks

Revision ID: 20260819_0005
Revises: 20260819_0004
Create Date: 2026-08-19 13:55:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260819_0005"
down_revision = "20260819_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vpn_configurations", sa.Column("desired_state", sa.Text(), nullable=True))
    op.add_column("vpn_configurations", sa.Column("generation", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE vpn_configurations
            SET desired_state = CASE
                WHEN status IN ('provisioning', 'active', 'failed') THEN 'active'
                ELSE 'revoked'
            END,
            generation = 1
            """
        )
    )
    op.alter_column("vpn_configurations", "desired_state", nullable=False, server_default=sa.text("'active'"))
    op.alter_column("vpn_configurations", "generation", nullable=False, server_default=sa.text("1"))
    op.create_check_constraint(
        "vpn_configurations_desired_state",
        "vpn_configurations",
        "desired_state IN ('active', 'revoked')",
    )
    op.create_check_constraint(
        "vpn_configurations_generation_positive",
        "vpn_configurations",
        "generation > 0",
    )

    op.add_column("node_tasks", sa.Column("vpn_generation", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE node_tasks AS nt
            SET vpn_generation = vc.generation
            FROM vpn_configurations AS vc
            WHERE nt.vpn_configuration_id = vc.vpn_configuration_id
              AND nt.vpn_configuration_id IS NOT NULL
            """
        )
    )
    op.create_check_constraint(
        "node_tasks_vpn_generation_positive",
        "node_tasks",
        "vpn_generation IS NULL OR vpn_generation > 0",
    )

    op.add_column("panel_provision_tasks", sa.Column("vpn_generation", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE panel_provision_tasks AS ppt
            SET vpn_generation = vc.generation
            FROM vpn_configurations AS vc
            WHERE ppt.vpn_configuration_id = vc.vpn_configuration_id
            """
        )
    )
    op.alter_column("panel_provision_tasks", "vpn_generation", nullable=False, server_default=sa.text("1"))
    op.create_check_constraint(
        "panel_provision_tasks_vpn_generation_positive",
        "panel_provision_tasks",
        "vpn_generation > 0",
    )

    op.create_table(
        "panel_revoke_tasks",
        sa.Column("panel_revoke_task_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("task_uuid", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("vpn_generation", sa.Integer(), nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_uuid", name="uq_panel_revoke_tasks_task_uuid"),
        sa.UniqueConstraint("idempotency_key", name="uq_panel_revoke_tasks_idempotency_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'cancelled')",
            name="panel_revoke_tasks_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="panel_revoke_tasks_attempts_non_negative"),
        sa.CheckConstraint("max_attempts > 0", name="panel_revoke_tasks_max_attempts_positive"),
        sa.CheckConstraint("vpn_generation > 0", name="panel_revoke_tasks_vpn_generation_positive"),
    )
    op.create_index(
        "idx_panel_revoke_tasks_status_next_retry_at",
        "panel_revoke_tasks",
        ["status", "next_retry_at"],
    )
    op.create_index(
        "idx_panel_revoke_tasks_status_lease_expires_at",
        "panel_revoke_tasks",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "idx_panel_revoke_tasks_vpn_generation",
        "panel_revoke_tasks",
        ["vpn_configuration_id", "vpn_generation"],
    )


def downgrade() -> None:
    op.drop_index("idx_panel_revoke_tasks_vpn_generation", table_name="panel_revoke_tasks")
    op.drop_index("idx_panel_revoke_tasks_status_lease_expires_at", table_name="panel_revoke_tasks")
    op.drop_index("idx_panel_revoke_tasks_status_next_retry_at", table_name="panel_revoke_tasks")
    op.drop_table("panel_revoke_tasks")

    op.drop_constraint(
        "panel_provision_tasks_vpn_generation_positive",
        "panel_provision_tasks",
        type_="check",
    )
    op.drop_column("panel_provision_tasks", "vpn_generation")

    op.drop_constraint("node_tasks_vpn_generation_positive", "node_tasks", type_="check")
    op.drop_column("node_tasks", "vpn_generation")

    op.drop_constraint("vpn_configurations_generation_positive", "vpn_configurations", type_="check")
    op.drop_constraint("vpn_configurations_desired_state", "vpn_configurations", type_="check")
    op.drop_column("vpn_configurations", "generation")
    op.drop_column("vpn_configurations", "desired_state")
