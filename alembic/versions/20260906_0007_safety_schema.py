"""Expand payment, node health and physical operation safety schema."""
from alembic import op
import sqlalchemy as sa

revision = "20260906_0007"
down_revision = "20260820_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        duplicate = bind.execute(sa.text("""
            SELECT vpn_configuration_id, operation, vpn_generation, count(*)
            FROM node_tasks
            WHERE vpn_configuration_id IS NOT NULL
            GROUP BY vpn_configuration_id, operation, vpn_generation
            HAVING count(*) > 1
            LIMIT 1
        """)).first()
        if duplicate is not None:
            raise RuntimeError("duplicate physical VPN operations prevent safety migration")
    op.create_index("uq_payment_events_provider_event_key", "payment_events", ["provider", "event_key"], unique=True)
    for table, column, typ in (("nodes", "last_checked_at", sa.DateTime(timezone=True)), ("nodes", "last_failed_at", sa.DateTime(timezone=True)), ("node_status", "last_checked_at", sa.DateTime(timezone=True)), ("node_status", "last_failed_at", sa.DateTime(timezone=True)), ("node_status", "active_clients", sa.Integer()), ("node_status", "max_clients", sa.Integer()), ("node_status", "probe_lease_token", sa.Uuid()), ("node_status", "probe_lease_expires_at", sa.DateTime(timezone=True)), ("node_tasks", "journal_retired_at", sa.DateTime(timezone=True))):
        op.add_column(table, sa.Column(column, typ, nullable=True))
    op.create_index("uq_node_tasks_vpn_operation_generation", "node_tasks", ["vpn_configuration_id", "operation", "vpn_generation"], unique=True, postgresql_where=sa.text("vpn_configuration_id IS NOT NULL"))
    op.create_index("uq_panel_revoke_tasks_vpn_generation", "panel_revoke_tasks", ["vpn_configuration_id", "vpn_generation"], unique=True)
    op.create_index("idx_node_status_last_checked_at", "node_status", ["last_checked_at"])
    op.create_index("idx_node_status_probe_lease_expires_at", "node_status", ["probe_lease_expires_at"])
    op.create_check_constraint("node_status_active_clients_nonnegative", "node_status", "active_clients IS NULL OR active_clients >= 0")
    op.create_check_constraint("node_status_max_clients_positive", "node_status", "max_clients IS NULL OR max_clients > 0")
    op.create_check_constraint("node_status_probe_lease_pair", "node_status", "(probe_lease_token IS NULL) = (probe_lease_expires_at IS NULL)")
    op.create_index("idx_node_tasks_terminal_unretired", "node_tasks", ["node_task_id"], postgresql_where=sa.text("status IN ('succeeded','failed','cancelled') AND journal_retired_at IS NULL"))


def downgrade() -> None:
    op.drop_index("idx_node_tasks_terminal_unretired", table_name="node_tasks")
    for name in ("node_status_probe_lease_pair", "node_status_max_clients_positive", "node_status_active_clients_nonnegative"):
        op.drop_constraint(name, "node_status", type_="check")
    for name, table in (("idx_node_status_probe_lease_expires_at", "node_status"), ("idx_node_status_last_checked_at", "node_status"), ("uq_panel_revoke_tasks_vpn_generation", "panel_revoke_tasks"), ("uq_node_tasks_vpn_operation_generation", "node_tasks"), ("uq_payment_events_provider_event_key", "payment_events")):
        op.drop_index(name, table_name=table)
    for table, column in (("node_tasks", "journal_retired_at"), ("node_status", "probe_lease_expires_at"), ("node_status", "probe_lease_token"), ("node_status", "max_clients"), ("node_status", "active_clients"), ("node_status", "last_failed_at"), ("node_status", "last_checked_at"), ("nodes", "last_failed_at"), ("nodes", "last_checked_at")):
        op.drop_column(table, column)
