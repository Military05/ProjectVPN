"""Complete the rolling audit-log migration for CHANGE-06."""

from alembic import op


revision = "20260913_0010"
down_revision = "20260906_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Install the mirror before the catch-up copy. CREATE TRIGGER takes a table
    # lock, so no legacy INSERT can fall into the gap between these two actions.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION shopbot_mirror_legacy_outbox_to_audit()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            INSERT INTO audit_events (
                event_name,
                aggregate_type,
                aggregate_id,
                payload,
                created_at,
                legacy_outbox_event_id
            )
            VALUES (
                NEW.event_name,
                NEW.aggregate_type,
                NEW.aggregate_id,
                NEW.payload,
                NEW.created_at,
                NEW.outbox_event_id
            )
            ON CONFLICT (legacy_outbox_event_id) DO NOTHING;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_outbox_events_audit_mirror
        AFTER INSERT ON outbox_events
        FOR EACH ROW
        EXECUTE FUNCTION shopbot_mirror_legacy_outbox_to_audit()
        """
    )
    op.execute(
        """
        INSERT INTO audit_events (
            event_name,
            aggregate_type,
            aggregate_id,
            payload,
            created_at,
            legacy_outbox_event_id
        )
        SELECT
            event_name,
            aggregate_type,
            aggregate_id,
            payload,
            created_at,
            outbox_event_id
        FROM outbox_events
        ON CONFLICT (legacy_outbox_event_id) DO NOTHING
        """
    )

    op.execute("DROP INDEX IF EXISTS idx_audit_events_aggregate")
    op.execute(
        """
        CREATE INDEX idx_audit_events_aggregate
        ON audit_events (aggregate_type, aggregate_id, created_at DESC)
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_audit_events_name_created")
    op.execute(
        """
        CREATE INDEX idx_audit_events_name_created
        ON audit_events (event_name, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION shopbot_reject_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events are immutable' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_immutable
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW
        EXECUTE FUNCTION shopbot_reject_audit_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS shopbot_reject_audit_event_mutation()")

    op.execute("DROP INDEX IF EXISTS idx_audit_events_name_created")
    op.execute(
        """
        CREATE INDEX idx_audit_events_name_created
        ON audit_events (event_name, created_at)
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_audit_events_aggregate")
    op.execute(
        """
        CREATE INDEX idx_audit_events_aggregate
        ON audit_events (aggregate_type, aggregate_id, created_at)
        """
    )

    op.execute("DROP TRIGGER IF EXISTS trg_outbox_events_audit_mirror ON outbox_events")
    op.execute("DROP FUNCTION IF EXISTS shopbot_mirror_legacy_outbox_to_audit()")
