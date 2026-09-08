"""add payment order provenance to subscription periods

Revision ID: 20260820_0006
Revises: 20260819_0005
"""
from alembic import op
import sqlalchemy as sa

revision = "20260820_0006"
down_revision = "20260819_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscription_periods", sa.Column("payment_order_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(
        "uq_subscription_periods_payment_order_id", "subscription_periods", ["payment_order_id"]
    )
    op.create_foreign_key(
        "fk_subscription_periods_payment_order_id",
        "subscription_periods", "payment_orders",
        ["payment_order_id"], ["payment_order_id"],
        onupdate="RESTRICT", ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_subscription_periods_payment_order_id", "subscription_periods", type_="foreignkey")
    op.drop_constraint("uq_subscription_periods_payment_order_id", "subscription_periods", type_="unique")
    op.drop_column("subscription_periods", "payment_order_id")
