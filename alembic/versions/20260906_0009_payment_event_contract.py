"""Drop legacy global payment event uniqueness after old replicas drain."""
from alembic import op

revision = "20260906_0009"
down_revision = "20260906_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_payment_events_event_key", table_name="payment_events")


def downgrade() -> None:
    op.create_index("uq_payment_events_event_key", "payment_events", ["event_key"], unique=True)
