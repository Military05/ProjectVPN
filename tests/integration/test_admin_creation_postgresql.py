from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shop_bot.application.use_cases.admin_operations import AdminOperations
from shop_bot.application.use_cases.node_administration import NodeAdministration
from shop_bot.apps.api.main import create_app
from shop_bot.apps.api.routes.admin import router as admin_router
from shop_bot.apps.api.routes.nodes import router as nodes_router
from shop_bot.core.config import Settings
from shop_bot.core.exceptions import ConflictError
from shop_bot.infrastructure.persistence.sqlalchemy.tables import (
    metadata,
    node_credentials,
    node_status,
    nodes,
    server_endpoints,
    servers,
    tariff_specs,
    tariffs,
)
from shop_bot.infrastructure.persistence.sqlalchemy.uow import SqlAlchemyUnitOfWork


DATABASE_ENV = "PROJECTVPN_CHANGE12_TEST_DATABASE_URL"
DATABASE_URL = os.getenv(DATABASE_ENV)
ADMIN_TOKEN = "change12-integration-admin-token"
TABLES = (
    tariffs,
    tariff_specs,
    servers,
    nodes,
    node_credentials,
    node_status,
    server_endpoints,
)

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.postgresql,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason=f"set {DATABASE_ENV} to a disposable PostgreSQL 15+ database",
    ),
]


class _NoopJobQueue:
    async def enqueue(self, *_args: object) -> None:
        return None


class _UnusedSyncStatus:
    async def execute(self, **_kwargs: object) -> dict[str, int]:
        raise AssertionError("node status sync is outside CHANGE-12 tests")


@dataclass(slots=True)
class _Harness:
    engine: AsyncEngine
    client: httpx.AsyncClient
    admin: AdminOperations
    node_admin: NodeAdministration


def _asyncpg_url(raw_url: str) -> URL:
    url = make_url(raw_url)
    if url.drivername in {"postgres", "postgresql"}:
        return url.set(drivername="postgresql+asyncpg")
    if url.drivername != "postgresql+asyncpg":
        raise ValueError(f"{DATABASE_ENV} must use PostgreSQL/asyncpg")
    return url


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def change12_harness() -> AsyncIterator[_Harness]:
    assert DATABASE_URL is not None
    database_url = _asyncpg_url(DATABASE_URL)
    schema_name = f"change12_{uuid4().hex}"
    admin_engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    engine: AsyncEngine | None = None

    try:
        async with admin_engine.connect() as connection:
            version = int(await connection.scalar(text("SHOW server_version_num")))
            assert version >= 150000, "CHANGE-12 endpoint semantics require PostgreSQL 15+"
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

        engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: metadata.create_all(
                    sync_connection,
                    tables=TABLES,
                )
            )

        uow_factory = lambda: SqlAlchemyUnitOfWork(engine)
        job_queue = _NoopJobQueue()
        admin = AdminOperations(
            uow_factory=uow_factory,
            job_queue=job_queue,
            reconcile_subscriptions=SimpleNamespace(),
            clock=lambda: datetime.now(UTC),
        )
        node_admin = NodeAdministration(
            uow_factory=uow_factory,
            job_queue=job_queue,
            sync_status=_UnusedSyncStatus(),
        )
        settings = Settings(
            admin_api_token=ADMIN_TOKEN,
            internal_api_key="change12-integration-internal-key",
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
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://change12.test",
        ) as client:
            yield _Harness(
                engine=engine,
                client=client,
                admin=admin,
                node_admin=node_admin,
            )
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await admin_engine.dispose()


async def _post_race(
    client: httpx.AsyncClient,
    path: str,
    payloads: tuple[dict[str, object], dict[str, object]],
) -> tuple[httpx.Response, httpx.Response]:
    barrier = asyncio.Barrier(2)

    async def submit(payload: dict[str, object]) -> httpx.Response:
        await barrier.wait()
        return await client.post(
            path,
            json=payload,
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    first, second = await asyncio.gather(*(submit(payload) for payload in payloads))
    assert sorted((first.status_code, second.status_code)) == [201, 409]
    assert 500 not in (first.status_code, second.status_code)
    return first, second


async def _count(engine: AsyncEngine, statement: object) -> int:
    async with engine.connect() as connection:
        return int(await connection.scalar(statement))


async def test_concurrent_tariff_creation_has_one_winner(change12_harness: _Harness) -> None:
    suffix = uuid4().hex
    tariff_name = f"change12-tariff-{suffix}"
    payload = {
        "tariff_name": tariff_name,
        "price_minor": 9900,
        "currency": "RUB",
        "period_days": 30,
        "description": "CHANGE-12 race test",
        "is_enabled": True,
    }

    await _post_race(change12_harness.client, "/admin/tariffs", (payload, payload))

    tariff_count = await _count(
        change12_harness.engine,
        select(func.count()).select_from(tariffs).where(tariffs.c.tariff_name == tariff_name),
    )
    specs_count = await _count(
        change12_harness.engine,
        select(func.count())
        .select_from(tariff_specs.join(tariffs))
        .where(tariffs.c.tariff_name == tariff_name),
    )
    assert tariff_count == 1
    assert specs_count == 1


async def test_concurrent_server_creation_covers_name_and_host(change12_harness: _Harness) -> None:
    suffix = uuid4().hex
    same_name = f"change12-server-name-{suffix}"
    name_payloads = (
        {"server_name": same_name, "host": f"a-{suffix}.example.test"},
        {"server_name": same_name, "host": f"b-{suffix}.example.test"},
    )
    await _post_race(change12_harness.client, "/admin/servers", name_payloads)
    assert await _count(
        change12_harness.engine,
        select(func.count()).select_from(servers).where(servers.c.server_name == same_name),
    ) == 1

    same_host = f"same-{suffix}.example.test"
    host_payloads = (
        {"server_name": f"change12-server-a-{suffix}", "host": same_host},
        {"server_name": f"change12-server-b-{suffix}", "host": same_host},
    )
    await _post_race(change12_harness.client, "/admin/servers", host_payloads)
    assert await _count(
        change12_harness.engine,
        select(func.count()).select_from(servers).where(servers.c.host == same_host),
    ) == 1


async def test_concurrent_node_creation_has_one_credential(change12_harness: _Harness) -> None:
    suffix = uuid4().hex
    node_key = f"change12-node-{suffix}"
    payloads = (
        {
            "node_key": node_key,
            "display_name": "CHANGE-12 node A",
            "api_base_url": f"https://node-a-{suffix}.example.test",
            "is_enabled": False,
            "selection_weight": 100,
            "key_id": "candidate-a",
            "shared_secret": f"change12-secret-a-{suffix}",
        },
        {
            "node_key": node_key,
            "display_name": "CHANGE-12 node B",
            "api_base_url": f"https://node-b-{suffix}.example.test",
            "is_enabled": False,
            "selection_weight": 100,
            "key_id": "candidate-b",
            "shared_secret": f"change12-secret-b-{suffix}",
        },
    )

    responses = await _post_race(change12_harness.client, "/admin/nodes", payloads)
    winner = next(response for response in responses if response.status_code == 201).json()

    async with change12_harness.engine.connect() as connection:
        rows = (
            await connection.execute(
                select(
                    nodes.c.node_id,
                    node_credentials.c.node_credential_id,
                    node_credentials.c.key_id,
                    node_credentials.c.shared_secret,
                )
                .join(node_credentials, node_credentials.c.node_id == nodes.c.node_id)
                .where(nodes.c.node_key == node_key)
            )
        ).mappings().all()

    assert len(rows) == 1
    assert winner["node"]["node_id"] == rows[0]["node_id"]
    assert winner["credential"]["node_credential_id"] == rows[0]["node_credential_id"]
    assert winner["credential"]["key_id"] == rows[0]["key_id"]
    assert winner["credential"]["shared_secret"] == rows[0]["shared_secret"]


async def test_concurrent_endpoint_creation_treats_nulls_as_equal(
    change12_harness: _Harness,
) -> None:
    suffix = uuid4().hex
    server = await change12_harness.admin.create_server(
        server_name=f"change12-endpoint-server-{suffix}",
        host=f"endpoint-{suffix}.example.test",
        is_enabled=True,
    )
    payload = {
        "server_id": int(server["server_id"]),
        "protocol": "vless",
        "port": 443,
        "is_enabled": True,
    }

    await _post_race(change12_harness.client, "/admin/server-endpoints", (payload, payload))

    assert await _count(
        change12_harness.engine,
        select(func.count())
        .select_from(server_endpoints)
        .where(
            server_endpoints.c.server_id == int(server["server_id"]),
            server_endpoints.c.protocol == "vless",
            server_endpoints.c.port == 443,
        ),
    ) == 1


async def test_tariff_and_node_children_roll_back_with_their_parent(
    change12_harness: _Harness,
) -> None:
    suffix = uuid4().hex
    tariff_name = f"change12-rollback-tariff-{suffix}"
    with pytest.raises(IntegrityError):
        await change12_harness.admin.create_tariff(
            tariff_name=tariff_name,
            price_minor=0,
            currency="RUB",
            period_days=30,
            description=None,
            is_enabled=True,
        )
    assert await _count(
        change12_harness.engine,
        select(func.count()).select_from(tariffs).where(tariffs.c.tariff_name == tariff_name),
    ) == 0

    node_key = f"change12-rollback-node-{suffix}"
    with pytest.raises(IntegrityError):
        await change12_harness.node_admin.create_node(
            node_key=node_key,
            display_name="CHANGE-12 rollback node",
            api_base_url="https://rollback-node.example.test",
            is_enabled=False,
            selection_weight=100,
            key_id="default",
            shared_secret="   ",
        )
    assert await _count(
        change12_harness.engine,
        select(func.count()).select_from(nodes).where(nodes.c.node_key == node_key),
    ) == 0


async def test_unrelated_check_and_foreign_key_violations_are_not_conflicts(
    change12_harness: _Harness,
) -> None:
    suffix = uuid4().hex
    with pytest.raises(IntegrityError):
        await change12_harness.admin.create_server(
            server_name="   ",
            host=f"invalid-{suffix}.example.test",
            is_enabled=True,
        )

    with pytest.raises(IntegrityError):
        await change12_harness.admin.create_server_endpoint(
            server_id=9_999_999_999,
            protocol="vless",
            port=443,
            node_id=None,
            local_inbound_id=None,
            security=None,
            sni=None,
            fingerprint=None,
            public_key=None,
            short_id=None,
            transport_type=None,
            flow=None,
            encryption=None,
            is_enabled=True,
        )
