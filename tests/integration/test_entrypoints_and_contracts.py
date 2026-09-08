from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute

from shop_bot.apps.api.main import create_app as create_api_app
from shop_bot.apps.node_agent.main import create_app as create_node_agent_app


ROOT = Path(__file__).resolve().parents[2]

API_CONTRACT = {
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/bot/tariffs"),
    ("GET", "/bot/dashboard"),
    ("POST", "/bot/register"),
    ("POST", "/bot/orders"),
    ("GET", "/admin/tariffs"),
    ("POST", "/admin/tariffs"),
    ("GET", "/admin/servers"),
    ("POST", "/admin/servers"),
    ("GET", "/admin/server-endpoints"),
    ("POST", "/admin/server-endpoints"),
    ("GET", "/admin/subscriptions"),
    ("GET", "/admin/vpn-configurations"),
    ("GET", "/admin/payment-orders"),
    ("POST", "/admin/payment-orders/{payment_order_id}/mark-paid"),
    ("POST", "/admin/subscriptions/{subscription_id}/provision"),
    ("POST", "/admin/vpn-configurations/{vpn_configuration_id}/revoke"),
    ("POST", "/admin/reconcile"),
    ("GET", "/admin/nodes"),
    ("POST", "/admin/nodes"),
    ("POST", "/admin/nodes/{node_id}/sync"),
    ("GET", "/admin/nodes/tasks"),
    ("POST", "/admin/nodes/tasks/{node_task_id}/dispatch"),
    ("POST", "/webhooks/{provider}"),
    ("POST", "/yookassa/webhook"),
    ("GET", "/sandbox/payments/{payment_order_id}"),
    ("POST", "/sandbox/payments/{payment_order_id}/pay"),
    ("GET", "/admin-ui"),
    ("GET", "/admin-ui/"),
}

NODE_AGENT_CONTRACT = {
    ("GET", "/agent/health"),
    ("GET", "/agent/capabilities"),
    ("GET", "/agent/status"),
    ("POST", "/agent/clients/provision"),
    ("POST", "/agent/clients/revoke"),
}


def _application_routes(app) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path.startswith(("/docs", "/redoc", "/openapi")):
            continue
        for method in route.methods:
            routes.add((method, route.path))
    return routes


def test_api_contract_routes_remain_registered() -> None:
    assert _application_routes(create_api_app()) == API_CONTRACT


def test_admin_static_assets_remain_mounted() -> None:
    mounts = {route.path for route in create_api_app().routes if route.__class__.__name__ == "Mount"}
    assert "/admin-ui/assets" in mounts


def test_node_agent_contract_routes_remain_registered() -> None:
    assert _application_routes(create_node_agent_app()) == NODE_AGENT_CONTRACT


def test_runtime_entrypoint_modules_and_scripts_exist() -> None:
    entrypoints = {
        "api": ROOT / "src/shop_bot/apps/api/__main__.py",
        "bot": ROOT / "src/shop_bot/apps/bot/__main__.py",
        "worker": ROOT / "src/shop_bot/apps/worker/__main__.py",
        "node_agent": ROOT / "src/shop_bot/apps/node_agent/__main__.py",
    }
    assert all(path.is_file() for path in entrypoints.values())

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for script in ("shopbot-api", "shopbot-bot", "shopbot-worker", "shopbot-node-agent"):
        assert f"{script} =" in pyproject


def _safe_production_settings():
    from shop_bot.core.config import Settings

    return Settings(
        app_env="production",
        internal_api_key="prod-internal",
        admin_api_token="prod-admin",
        payment_default_provider="yookassa",
        yookassa_shop_id="prod-shop",
        yookassa_secret_key="prod-yookassa-secret",
        panel_mode="xui",
        xui_base_url="https://xui.example.test",
        xui_username="prod-xui",
        xui_password="prod-xui-secret",
        database_url="postgresql+asyncpg://shopbot:prod-db-secret@postgres:5432/shopbot",
        node_agent_shared_secret="prod-node-secret",
        demo_node_shared_secret="prod-demo-secret",
        node_agent_runtime_mode="xui",
        auto_seed_demo_data=False,
        demo_node_auto_register=False,
    )


def test_production_api_has_no_sandbox_routes_or_openapi_operations() -> None:
    app = create_api_app(settings=_safe_production_settings())
    route_paths = {route.path for route in app.routes}
    assert all(not path.startswith("/sandbox/") for path in route_paths)
    assert all(not path.startswith("/sandbox/") for path in app.openapi()["paths"])


def test_worker_registers_stale_node_task_reaper() -> None:
    worker_source = (ROOT / "src/shop_bot/apps/worker/main.py").read_text(encoding="utf-8")
    assert "async def recover_stale_node_tasks_job" in worker_source
    assert "recover_stale_node_tasks_job," in worker_source
    assert "cron(recover_stale_node_tasks_job, minute=set(range(60)))" in worker_source
