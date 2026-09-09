from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from shop_bot.application.queries.services import NodeQueryService
from shop_bot.application.use_cases.admin_operations import AdminOperations
from shop_bot.apps.api.routes.admin import (
    list_payment_orders,
    list_subscriptions,
    list_vpn_configurations,
    router as admin_router,
)
from shop_bot.apps.api.routes.nodes import get_nodes, get_tasks, router as nodes_router
from shop_bot.infrastructure.persistence.repositories.nodes import NodeRepository
from shop_bot.infrastructure.persistence.repositories.payments import PaymentRepository
from shop_bot.infrastructure.persistence.repositories.subscriptions import SubscriptionRepository
from shop_bot.infrastructure.persistence.repositories.vpn import VpnRepository


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
PAGINATED_PATHS = (
    "/admin/subscriptions",
    "/admin/vpn-configurations",
    "/admin/payment-orders",
    "/admin/nodes",
    "/admin/nodes/tasks",
)


def _node_row() -> dict[str, Any]:
    return {
        "node_id": 1,
        "node_key": "node-1",
        "display_name": "Node 1",
        "api_base_url": "https://node.example.test",
        "status": "online",
        "is_enabled": True,
        "selection_weight": 100,
        "last_seen_at": NOW,
        "last_error": None,
        "created_at": NOW,
        "updated_at": NOW,
        "health_status": "online",
        "agent_version": "1.0.0",
    }


def _node_task_row() -> dict[str, Any]:
    return {
        "node_task_id": 1,
        "node_id": 1,
        "operation": "provision",
        "status": "pending",
        "idempotency_key": "task-1",
        "vpn_configuration_id": 1,
        "subscription_id": 1,
        "attempts": 0,
        "max_attempts": 5,
        "next_retry_at": NOW,
        "last_error": None,
        "completed_at": None,
    }


def test_large_admin_lists_publish_backward_compatible_query_contract() -> None:
    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(nodes_router)
    openapi = app.openapi()

    for path in PAGINATED_PATHS:
        operation = openapi["paths"][path]["get"]
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert parameters["limit"]["schema"] == {
            "type": "integer",
            "maximum": 200,
            "minimum": 1,
            "default": 100,
            "title": "Limit",
        }
        assert parameters["offset"]["schema"] == {
            "type": "integer",
            "minimum": 0,
            "default": 0,
            "title": "Offset",
        }


def test_large_admin_list_http_defaults_and_bounds_remain_compatible() -> None:
    admin = SimpleNamespace(
        list_subscriptions=AsyncMock(return_value=[]),
        list_vpn_configurations=AsyncMock(return_value=[]),
        list_payment_orders=AsyncMock(return_value=[]),
    )
    nodes = SimpleNamespace(
        list_nodes=AsyncMock(return_value=[]),
        list_tasks=AsyncMock(return_value=[]),
    )
    app = FastAPI()
    app.state.settings = SimpleNamespace(admin_api_token="test-admin-token")
    app.state.container = SimpleNamespace(
        applications=SimpleNamespace(admin=admin),
        queries=SimpleNamespace(nodes=nodes),
    )
    app.include_router(admin_router)
    app.include_router(nodes_router)
    client = TestClient(app)
    headers = {"X-Admin-Token": "test-admin-token"}

    for path in PAGINATED_PATHS:
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json() == []
        assert response.headers["X-Page-Limit"] == "100"
        assert response.headers["X-Page-Offset"] == "0"
        assert response.headers["X-Has-More"] == "false"
        assert client.get(path, headers=headers, params={"limit": 0}).status_code == 422
        assert client.get(path, headers=headers, params={"limit": 201}).status_code == 422
        assert client.get(path, headers=headers, params={"offset": -1}).status_code == 422


@pytest.mark.parametrize(
    ("handler", "container_branch", "method_name", "row"),
    (
        (list_subscriptions, "admin", "list_subscriptions", {"subscription_id": 1}),
        (list_vpn_configurations, "admin", "list_vpn_configurations", {"vpn_configuration_id": 1}),
        (list_payment_orders, "admin", "list_payment_orders", {"payment_order_id": 1}),
        (get_nodes, "nodes", "list_nodes", _node_row()),
        (get_tasks, "nodes", "list_tasks", _node_task_row()),
    ),
)
async def test_admin_list_routes_use_probe_row_and_return_page_headers(
    handler: Any,
    container_branch: str,
    method_name: str,
    row: dict[str, Any],
) -> None:
    list_method = AsyncMock(return_value=[row, row, row])
    source = SimpleNamespace(**{method_name: list_method})
    if container_branch == "admin":
        container = SimpleNamespace(applications=SimpleNamespace(admin=source))
    else:
        container = SimpleNamespace(queries=SimpleNamespace(nodes=source))
    response = Response()

    result = await handler(response=response, limit=2, offset=100, container=container)

    list_method.assert_awaited_once_with(limit=3, offset=100)
    assert len(result) == 2
    assert response.headers["X-Page-Limit"] == "2"
    assert response.headers["X-Page-Offset"] == "100"
    assert response.headers["X-Has-More"] == "true"


class _UowContext:
    def __init__(self, uow: Any) -> None:
        self.uow = uow

    async def __aenter__(self) -> Any:
        return self.uow

    async def __aexit__(self, *_args: Any) -> None:
        return None


@pytest.mark.parametrize(
    ("service_kind", "repository_name", "service_method", "repository_method"),
    (
        ("admin", "subscriptions", "list_subscriptions", "list_subscriptions"),
        ("admin", "vpn", "list_vpn_configurations", "list_configurations"),
        ("admin", "payments", "list_payment_orders", "list_orders"),
        ("nodes", "nodes", "list_nodes", "list_nodes"),
        ("nodes", "nodes", "list_tasks", "list_tasks"),
    ),
)
async def test_application_list_services_forward_page_window(
    service_kind: str,
    repository_name: str,
    service_method: str,
    repository_method: str,
) -> None:
    list_method = AsyncMock(return_value=[{"id": 1}])
    repository = SimpleNamespace(**{repository_method: list_method})
    uow = SimpleNamespace(**{repository_name: repository})

    def uow_factory() -> _UowContext:
        return _UowContext(uow)

    if service_kind == "admin":
        service = AdminOperations(
            uow_factory=uow_factory,
            job_queue=SimpleNamespace(),
            reconcile_subscriptions=SimpleNamespace(),
            clock=lambda: NOW,
        )
    else:
        service = NodeQueryService(uow_factory=uow_factory)

    result = await getattr(service, service_method)(limit=51, offset=100)

    assert result == [{"id": 1}]
    list_method.assert_awaited_once_with(limit=51, offset=100)


class _EmptyMappingsResult:
    def mappings(self) -> _EmptyMappingsResult:
        return self

    def all(self) -> list[Any]:
        return []


class _RecordingConnection:
    def __init__(self) -> None:
        self.statement: Any = None

    async def execute(self, statement: Any) -> _EmptyMappingsResult:
        self.statement = statement
        return _EmptyMappingsResult()


@pytest.mark.parametrize(
    ("repository_type", "method_name", "stable_order"),
    (
        (
            SubscriptionRepository,
            "list_subscriptions",
            "ORDER BY subscriptions.created_at DESC, subscriptions.subscription_id DESC",
        ),
        (
            VpnRepository,
            "list_configurations",
            "ORDER BY vpn_configurations.created_at DESC, vpn_configurations.vpn_configuration_id DESC",
        ),
        (
            PaymentRepository,
            "list_orders",
            "ORDER BY payment_orders.created_at DESC, payment_orders.payment_order_id DESC",
        ),
        (NodeRepository, "list_nodes", "ORDER BY nodes.node_id ASC"),
        (
            NodeRepository,
            "list_tasks",
            "ORDER BY node_tasks.created_at DESC, node_tasks.node_task_id DESC",
        ),
    ),
)
async def test_admin_list_repositories_apply_window_and_stable_pk_order(
    repository_type: Any,
    method_name: str,
    stable_order: str,
) -> None:
    connection = _RecordingConnection()
    repository = repository_type(connection)

    await getattr(repository, method_name)(limit=51, offset=100)

    sql = " ".join(
        str(
            connection.statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).split()
    )
    assert stable_order in sql
    assert "LIMIT 51 OFFSET 100" in sql


def test_admin_ui_uses_server_pages_of_fifty_without_full_dataset_claims() -> None:
    source = (ROOT / "src/shop_bot/apps/api/static/admin/assets/app.js").read_text(encoding="utf-8")

    assert "const SERVER_PAGE_SIZE = 50;" in source
    for dataset in ("subscriptions", "vpn", "payments", "nodes", "tasks"):
        assert f'    "{dataset}",' in source
    assert "?limit=${SERVER_PAGE_SIZE}&offset=${requestedPage.offset}" in source
    assert 'response.headers.get("X-Has-More")' in source
    assert "currentPage.offset + SERVER_PAGE_SIZE" in source
    assert "Math.max(currentPage.offset - SERVER_PAGE_SIZE, 0)" in source
    assert "это не полный список" in source
