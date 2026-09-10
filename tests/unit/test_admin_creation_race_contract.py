from __future__ import annotations

from inspect import getsource
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from shop_bot.application.use_cases.node_administration import NodeAdministration
from shop_bot.apps.api.main import create_app
from shop_bot.apps.api.routes.admin import router as admin_router
from shop_bot.apps.api.routes.nodes import router as nodes_router
from shop_bot.core.config import Settings
from shop_bot.core.exceptions import ConflictError
from shop_bot.infrastructure.persistence.repositories.admin import AdminRepository
from shop_bot.infrastructure.persistence.repositories.nodes import NodeRepository
from shop_bot.infrastructure.persistence.repositories.servers import ServerRepository


ADMIN_TOKEN = "change12-unit-admin-token"


class _EmptyResult:
    def first(self) -> None:
        return None

    def mappings(self) -> _EmptyResult:
        return self


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _EmptyResult:
        self.statements.append(statement)
        return _EmptyResult()


@pytest.mark.parametrize(
    ("repository_type", "method_name", "payload", "conflict_clause"),
    (
        (
            AdminRepository,
            "create_tariff",
            {
                "tariff_name": "change12-tariff",
                "price_minor": 9900,
                "currency": "RUB",
                "period_days": 30,
                "description": None,
            },
            "ON CONFLICT (tariff_name) DO NOTHING",
        ),
        (
            ServerRepository,
            "create_server",
            {"server_name": "change12-server", "host": "server.example.test"},
            "ON CONFLICT DO NOTHING",
        ),
        (
            NodeRepository,
            "create_node",
            {
                "node_key": "change12-node",
                "display_name": "CHANGE-12 node",
                "api_base_url": "https://node.example.test",
            },
            "ON CONFLICT (node_key) DO NOTHING",
        ),
        (
            ServerRepository,
            "create_server_endpoint",
            {"server_id": 1, "protocol": "vless", "port": 443},
            "ON CONFLICT ON CONSTRAINT uq_server_endpoints_full_tuple DO NOTHING",
        ),
    ),
)
async def test_admin_create_uses_database_conflict_arbitration(
    repository_type: type[Any],
    method_name: str,
    payload: dict[str, Any],
    conflict_clause: str,
) -> None:
    connection = _RecordingConnection()
    repository = repository_type(connection)

    with pytest.raises(ConflictError):
        await getattr(repository, method_name)(**payload)

    assert len(connection.statements) == 1
    sql = " ".join(
        str(
            connection.statements[0].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).split()
    )
    assert conflict_clause in sql
    assert "RETURNING" in sql


class _UowContext:
    def __init__(self, uow: Any) -> None:
        self.uow = uow

    async def __aenter__(self) -> Any:
        return self.uow

    async def __aexit__(self, *_args: Any) -> None:
        return None


async def test_losing_node_insert_never_attempts_credential_creation() -> None:
    create_node = AsyncMock(side_effect=ConflictError("duplicate node"))
    create_credential = AsyncMock()
    uow = SimpleNamespace(
        nodes=SimpleNamespace(
            create_node=create_node,
            create_credential=create_credential,
        )
    )
    use_case = NodeAdministration(
        uow_factory=lambda: _UowContext(uow),
        job_queue=SimpleNamespace(),
        sync_status=SimpleNamespace(),
    )

    with pytest.raises(ConflictError, match="duplicate node"):
        await use_case.create_node(
            node_key="change12-node",
            display_name="CHANGE-12 node",
            api_base_url="https://node.example.test",
            is_enabled=False,
            selection_weight=100,
            key_id="default",
            shared_secret="change12-unit-secret",
        )

    create_node.assert_awaited_once()
    create_credential.assert_not_awaited()


@pytest.mark.parametrize(
    ("path", "payload"),
    (
        (
            "/admin/tariffs",
            {
                "tariff_name": "change12-tariff",
                "price_minor": 9900,
                "currency": "RUB",
                "period_days": 30,
            },
        ),
        (
            "/admin/servers",
            {"server_name": "change12-server", "host": "server.example.test"},
        ),
        (
            "/admin/server-endpoints",
            {"server_id": 1, "protocol": "vless", "port": 443},
        ),
        (
            "/admin/nodes",
            {
                "node_key": "change12-node",
                "display_name": "CHANGE-12 node",
                "api_base_url": "https://node.example.test",
                "is_enabled": False,
                "selection_weight": 100,
                "key_id": "default",
                "shared_secret": "change12-unit-secret",
            },
        ),
    ),
)
def test_domain_conflicts_are_exposed_as_http_409(path: str, payload: dict[str, Any]) -> None:
    conflict = ConflictError("CHANGE-12 conflict")
    admin = SimpleNamespace(
        create_tariff=AsyncMock(side_effect=conflict),
        create_server=AsyncMock(side_effect=conflict),
        create_server_endpoint=AsyncMock(side_effect=conflict),
    )
    node_admin = SimpleNamespace(create_node=AsyncMock(side_effect=conflict))
    settings = Settings(
        admin_api_token=ADMIN_TOKEN,
        internal_api_key="change12-unit-internal-key",
    )
    production_app = create_app(settings=settings)
    app = FastAPI()
    app.state.settings = settings
    app.state.container = SimpleNamespace(
        applications=SimpleNamespace(admin=admin, node_admin=node_admin)
    )
    app.include_router(admin_router)
    app.include_router(nodes_router)
    app.add_exception_handler(
        ConflictError,
        production_app.exception_handlers[ConflictError],
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        path,
        json=payload,
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "CHANGE-12 conflict"}


def test_repositories_do_not_catch_integrity_errors_as_domain_conflicts() -> None:
    sources = (
        getsource(AdminRepository.create_tariff),
        getsource(ServerRepository.create_server),
        getsource(ServerRepository.create_server_endpoint),
        getsource(NodeRepository.create_node),
    )

    assert all("IntegrityError" not in source for source in sources)
