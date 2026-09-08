"""payment security canonical fields and node task leases

Revision ID: 20260819_0003
Revises: 20260425_0002
Create Date: 2026-08-19 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260819_0003"
down_revision = "20260425_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_events", sa.Column("provider_payment_id", sa.Text(), nullable=True))
    op.add_column("payment_events", sa.Column("reported_payment_order_id", sa.BigInteger(), nullable=True))
    op.add_column("payment_events", sa.Column("payment_status", sa.Text(), nullable=True))
    op.add_column("payment_events", sa.Column("amount_minor", sa.BigInteger(), nullable=True))
    op.add_column("payment_events", sa.Column("currency", sa.Text(), nullable=True))

    op.add_column("node_tasks", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("node_tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("node_tasks", sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(
        "idx_node_tasks_status_lease_expires_at",
        "node_tasks",
        ["status", "lease_expires_at"],
    )

    # Pre-lease IN_PROGRESS rows are abandoned claims. Close their dangling current
    # attempt first, then make the same task retryable/terminal without changing its
    # idempotency key or attempt count.
    op.execute(
        """
        UPDATE node_task_attempts AS a
        SET status = 'failed',
            finished_at = CURRENT_TIMESTAMP,
            error_message = 'node task lease recovered during migration'
        FROM node_tasks AS t
        WHERE a.node_task_id = t.node_task_id
          AND a.attempt_no = t.attempts
          AND a.status = 'started'
          AND a.finished_at IS NULL
          AND t.status = 'in_progress'
          AND t.lease_expires_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE node_tasks
        SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
            next_retry_at = CURRENT_TIMESTAMP,
            last_error = 'node task lease recovered during migration',
            claimed_at = NULL,
            lease_expires_at = NULL,
            lease_token = NULL,
            completed_at = CASE
                WHEN attempts >= max_attempts THEN CURRENT_TIMESTAMP
                ELSE NULL
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'in_progress'
          AND lease_expires_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("idx_node_tasks_status_lease_expires_at", table_name="node_tasks")
    op.drop_column("node_tasks", "lease_token")
    op.drop_column("node_tasks", "lease_expires_at")
    op.drop_column("node_tasks", "claimed_at")

    op.drop_column("payment_events", "currency")
    op.drop_column("payment_events", "amount_minor")
    op.drop_column("payment_events", "payment_status")
    op.drop_column("payment_events", "reported_payment_order_id")
    op.drop_column("payment_events", "provider_payment_id")
