from shop_bot.infrastructure.persistence.sqlalchemy.tables import metadata


def test_required_tables_exist_in_metadata() -> None:
    expected = {
        "users",
        "user_contacts",
        "tariffs",
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
        "tariff_specs",
        "nodes",
        "node_credentials",
        "node_status",
        "node_tasks",
        "node_task_attempts",
    }
    assert expected.issubset(set(metadata.tables))


def test_payment_event_canonical_fields_and_node_lease_fields_are_in_metadata() -> None:
    payment_events = metadata.tables["payment_events"]
    assert {"provider_payment_id", "reported_payment_order_id", "payment_status", "amount_minor", "currency"}.issubset(payment_events.c.keys())
    node_tasks = metadata.tables["node_tasks"]
    assert {"claimed_at", "lease_expires_at", "lease_token"}.issubset(node_tasks.c.keys())


def test_payment_claim_and_panel_task_fields_are_in_metadata() -> None:
    payment_attempts = metadata.tables["payment_attempts"]
    assert {"creation_claimed_at", "creation_lease_expires_at", "creation_lease_token"}.issubset(payment_attempts.c.keys())
    panel_tasks = metadata.tables["panel_provision_tasks"]
    assert {"lease_token", "lease_expires_at", "compensation_required"}.issubset(panel_tasks.c.keys())
