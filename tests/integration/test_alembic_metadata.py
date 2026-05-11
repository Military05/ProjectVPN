from shop_bot.infrastructure.db.tables import metadata


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
        "tariff_specs",
        "nodes",
        "node_credentials",
        "node_status",
        "node_tasks",
        "node_task_attempts",
    }
    assert expected.issubset(set(metadata.tables))
