"""Add maintenance leases and immutable audit events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260906_0008"
down_revision = "20260906_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("maintenance_leases",
        sa.Column("lease_name", sa.Text(), primary_key=True),
        sa.Column("owner_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table("audit_events",
        sa.Column("audit_event_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("legacy_outbox_event_id", sa.BigInteger(), unique=True),
    )
    op.create_index("idx_audit_events_aggregate", "audit_events", ["aggregate_type", "aggregate_id", "created_at"], postgresql_using="btree")
    op.create_index("idx_audit_events_name_created", "audit_events", ["event_name", "created_at"])
    op.execute("""
        INSERT INTO audit_events(event_name, aggregate_type, aggregate_id, payload, created_at, legacy_outbox_event_id)
        SELECT event_name, aggregate_type, aggregate_id, payload, created_at, outbox_event_id FROM outbox_events
        ON CONFLICT (legacy_outbox_event_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("idx_audit_events_name_created", table_name="audit_events")
    op.drop_index("idx_audit_events_aggregate", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("maintenance_leases")
