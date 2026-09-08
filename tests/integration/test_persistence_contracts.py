from __future__ import annotations

from pathlib import Path

from shop_bot.infrastructure.persistence.sqlalchemy.tables import metadata


ROOT = Path(__file__).resolve().parents[2]


EXPECTED_TABLES = {
    "users",
    "user_contacts",
    "tariffs",
    "tariff_specs",
    "subscriptions",
    "subscription_periods",
    "servers",
    "server_endpoints",
    "vpn_configurations",
    "payment_orders",
    "payment_attempts",
    "payment_events",
    "provider_transactions",
    "webhook_inbox",
    "outbox_events",
    "panel_provision_tasks",
    "panel_revoke_tasks",
    "nodes",
    "node_credentials",
    "node_status",
    "node_tasks",
    "node_task_attempts",
}


def test_database_table_contract_is_preserved() -> None:
    assert set(metadata.tables) == EXPECTED_TABLES


def test_existing_alembic_revisions_are_preserved() -> None:
    versions = ROOT / "alembic" / "versions"
    revision_files = {path.name for path in versions.glob("*.py")}
    assert "20260419_0001_initial_schema.py" in revision_files
    assert "20260425_0002_multinode_architecture.py" in revision_files


def test_p0_additive_columns_and_migration_contract() -> None:
    payment_events = metadata.tables["payment_events"]
    assert {
        "provider_payment_id",
        "reported_payment_order_id",
        "payment_status",
        "amount_minor",
        "currency",
    }.issubset(payment_events.c.keys())

    node_tasks = metadata.tables["node_tasks"]
    assert {"claimed_at", "lease_expires_at", "lease_token"}.issubset(node_tasks.c.keys())
    index_names = {index.name for index in node_tasks.indexes}
    assert "idx_node_tasks_status_lease_expires_at" in index_names

    migration = ROOT / "alembic" / "versions" / "20260819_0003_payment_security_node_leases.py"
    text = migration.read_text(encoding="utf-8")
    assert 'down_revision = "20260425_0002"' in text
    assert "node task lease recovered during migration" in text
    assert "a.attempt_no = t.attempts" in text
    assert "t.status = 'in_progress'" in text


def test_payment_event_canonical_fields_round_trip_through_mapper() -> None:
    from datetime import UTC, datetime

    from shop_bot.infrastructure.persistence.sqlalchemy.mappers import payment_event_from_row

    now = datetime(2026, 8, 19, tzinfo=UTC)
    event = payment_event_from_row(
        {
            "payment_event_id": 1,
            "payment_order_id": 2,
            "payment_attempt_id": 3,
            "provider": "cryptobot",
            "event_type": "invoice_paid",
            "event_key": "evt-1",
            "provider_payment_id": "pay-1",
            "reported_payment_order_id": 2,
            "payment_status": "paid",
            "amount_minor": 9900,
            "currency": "rub",
            "status": "received",
            "payload": {"verified": True},
            "occurred_at": now,
            "received_at": now,
            "processed_at": None,
            "error_message": None,
        }
    )
    assert event.provider_payment_id == "pay-1"
    assert event.reported_payment_order_id == 2
    assert str(event.payment_status) == "paid"
    assert event.amount_minor == 9900
    assert event.currency == "RUB"


def test_node_reaper_repository_uses_skip_locked_fencing_scan() -> None:
    import inspect

    from shop_bot.infrastructure.persistence.repositories.nodes import NodeRepository

    source = inspect.getsource(NodeRepository.list_stale_task_entities)
    assert 'status == "in_progress"' in source
    assert "lease_expires_at <= now" in source
    assert "with_for_update(skip_locked=True)" in source


def test_p0_payment_claim_and_panel_task_schema_contract() -> None:
    payment_attempts = metadata.tables["payment_attempts"]
    assert {
        "creation_claimed_at",
        "creation_lease_expires_at",
        "creation_lease_token",
    }.issubset(payment_attempts.c.keys())
    assert "uq_payment_attempts_one_creation_claim_per_order" in {
        index.name for index in payment_attempts.indexes
    }

    panel_tasks = metadata.tables["panel_provision_tasks"]
    assert {
        "vpn_configuration_id",
        "subscription_id",
        "status",
        "idempotency_key",
        "payload",
        "attempts",
        "max_attempts",
        "next_retry_at",
        "last_error",
        "claimed_at",
        "lease_expires_at",
        "lease_token",
        "compensation_required",
        "created_at",
        "updated_at",
        "completed_at",
    }.issubset(panel_tasks.c.keys())
    index_names = {index.name for index in panel_tasks.indexes}
    assert "idx_panel_provision_tasks_status_next_retry_at" in index_names
    assert "idx_panel_provision_tasks_status_lease_expires_at" in index_names

    migration = ROOT / "alembic" / "versions" / "20260819_0004_payment_claims_panel_provision_tasks.py"
    text = migration.read_text(encoding="utf-8")
    assert 'down_revision = "20260819_0003"' in text
    assert "uq_payment_attempts_one_creation_claim_per_order" in text
    assert "panel_provision_tasks" in text


def test_vpn_generation_and_panel_revoke_schema_contract() -> None:
    vpn = metadata.tables["vpn_configurations"]
    assert {"desired_state", "generation"}.issubset(vpn.c.keys())

    node_tasks = metadata.tables["node_tasks"]
    assert "vpn_generation" in node_tasks.c

    panel_provision = metadata.tables["panel_provision_tasks"]
    assert "vpn_generation" in panel_provision.c

    panel_revoke = metadata.tables["panel_revoke_tasks"]
    assert {
        "panel_revoke_task_id",
        "task_uuid",
        "vpn_configuration_id",
        "subscription_id",
        "vpn_generation",
        "status",
        "idempotency_key",
        "payload",
        "attempts",
        "max_attempts",
        "next_retry_at",
        "claimed_at",
        "lease_expires_at",
        "lease_token",
        "created_at",
        "updated_at",
        "completed_at",
    }.issubset(panel_revoke.c.keys())
    index_names = {index.name for index in panel_revoke.indexes}
    assert "idx_panel_revoke_tasks_status_next_retry_at" in index_names
    assert "idx_panel_revoke_tasks_status_lease_expires_at" in index_names
    assert "idx_panel_revoke_tasks_vpn_generation" in index_names

    migration = ROOT / "alembic" / "versions" / "20260819_0005_vpn_generation_panel_revoke.py"
    text = migration.read_text(encoding="utf-8")
    assert 'down_revision = "20260819_0004"' in text
    assert "desired_state" in text
    assert "generation" in text
    assert "panel_revoke_tasks" in text
    assert "WHEN status IN ('provisioning', 'active', 'failed') THEN 'active'" in text
    assert "generation = 1" in text


def test_vpn_generation_fields_round_trip_through_mapper() -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    from shop_bot.domain.entities.vpn import VpnDesiredState
    from shop_bot.infrastructure.persistence.sqlalchemy.mappers import vpn_configuration_from_row

    now = datetime(2026, 8, 19, tzinfo=UTC)
    entity = vpn_configuration_from_row(
        {
            "vpn_configuration_id": 7,
            "subscription_id": 1,
            "server_endpoint_id": 9,
            "client_uuid": UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            "display_name": "vpn-1",
            "status": "revoking",
            "remote_client_ref": "remote-1",
            "created_at": now,
            "revoked_at": None,
            "desired_state": "revoked",
            "generation": 4,
        }
    )
    assert entity.desired_state is VpnDesiredState.REVOKED
    assert entity.generation == 4


def test_admin_vpn_list_query_does_not_expose_internal_fencing_columns() -> None:
    import inspect

    from shop_bot.infrastructure.persistence.repositories.vpn import VpnRepository

    source = inspect.getsource(VpnRepository.list_configurations)
    assert "vpn_configurations.c.vpn_configuration_id" in source
    assert "vpn_configurations.c.desired_state" not in source
    assert "vpn_configurations.c.generation" not in source
