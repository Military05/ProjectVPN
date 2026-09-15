from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shop_bot.application.use_cases.provision_vpn import ProvisionVpn
from shop_bot.domain.services.vpn_provisioning import VpnProvisioningService
from shop_bot.domain.vpn.builder import VlessUriBuilder
from shop_bot.infrastructure.persistence.repositories.servers import ServerRepository
from shop_bot.infrastructure.persistence.sqlalchemy.tables import (
    audit_events,
    metadata,
    node_status,
    node_tasks,
    nodes,
    server_endpoints,
    servers,
    subscription_periods,
    subscriptions,
    tariffs,
    users,
    vpn_configurations,
)
from shop_bot.infrastructure.persistence.sqlalchemy.uow import SqlAlchemyUnitOfWork


DATABASE_ENV = "PROJECTVPN_CHANGE03_TEST_DATABASE_URL"
DATABASE_URL = os.getenv(DATABASE_ENV)

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.postgresql,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason=f"set {DATABASE_ENV} to a disposable PostgreSQL 15+ database",
    ),
]


class _Queue:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, tuple[Any, ...]]] = []

    async def enqueue(self, job_name: Any, *args: Any) -> None:
        self.jobs.append((job_name, args))


class _BarrierServerRepository(ServerRepository):
    def __init__(self, connection: Any, barrier: asyncio.Barrier) -> None:
        super().__init__(connection)
        self._barrier = barrier

    async def list_node_selection_candidates(self) -> list[dict[str, Any]]:
        rows = await super().list_node_selection_candidates()
        # Both transactions take the same pre-lock capacity snapshot. Only the
        # node-row lock plus the post-lock snapshot may arbitrate the final slot.
        await self._barrier.wait()
        return [dict(row) for row in rows]


class _BarrierUow(SqlAlchemyUnitOfWork):
    def __init__(self, engine: AsyncEngine, barrier: asyncio.Barrier) -> None:
        super().__init__(engine)
        self._barrier = barrier

    async def __aenter__(self) -> "_BarrierUow":
        await super().__aenter__()
        assert self.connection is not None
        self.servers = _BarrierServerRepository(self.connection, self._barrier)
        return self


@dataclass(frozen=True, slots=True)
class _Harness:
    engine: AsyncEngine
    subscription_ids: tuple[int, int]
    now: datetime


def _asyncpg_url(raw_url: str) -> URL:
    url = make_url(raw_url)
    if url.drivername in {"postgres", "postgresql"}:
        return url.set(drivername="postgresql+asyncpg")
    if url.drivername != "postgresql+asyncpg":
        raise ValueError(f"{DATABASE_ENV} must use PostgreSQL/asyncpg")
    return url


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def change03_harness() -> AsyncIterator[_Harness]:
    assert DATABASE_URL is not None
    database_url = _asyncpg_url(DATABASE_URL)
    schema_name = f"change03_{uuid4().hex}"
    admin_engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    engine: AsyncEngine | None = None

    try:
        async with admin_engine.connect() as connection:
            version = int(await connection.scalar(text("SHOW server_version_num")))
            assert version >= 150000
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

        engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            now = datetime.now(UTC)
            tariff_id = int(
                await connection.scalar(
                    tariffs.insert()
                    .values(tariff_name=f"change03-{uuid4().hex}")
                    .returning(tariffs.c.tariff_id)
                )
            )
            user_ids = []
            for suffix in ("a", "b"):
                user_ids.append(
                    int(
                        await connection.scalar(
                            users.insert()
                            .values(name_or_nick=f"change03-user-{suffix}-{uuid4().hex}")
                            .returning(users.c.user_id)
                        )
                    )
                )
            subscription_ids = []
            for user_id in user_ids:
                subscription_id = int(
                    await connection.scalar(
                        subscriptions.insert()
                        .values(user_id=user_id, tariff_id=tariff_id, status="active")
                        .returning(subscriptions.c.subscription_id)
                    )
                )
                subscription_ids.append(subscription_id)
                await connection.execute(
                    subscription_periods.insert().values(
                        subscription_id=subscription_id,
                        starts_at=now - timedelta(days=1),
                        expires_at=now + timedelta(days=30),
                        is_paid=True,
                    )
                )

            node_id = int(
                await connection.scalar(
                    nodes.insert()
                    .values(
                        node_key=f"change03-node-{uuid4().hex}",
                        display_name="CHANGE-03 final-slot node",
                        api_base_url="https://change03-node.example.test",
                        status="online",
                        is_enabled=True,
                        selection_weight=100,
                        last_seen_at=now,
                        last_checked_at=now,
                    )
                    .returning(nodes.c.node_id)
                )
            )
            await connection.execute(
                node_status.insert().values(
                    node_id=node_id,
                    health_status="online",
                    last_seen_at=now,
                    last_checked_at=now,
                    active_clients=0,
                    max_clients=1,
                )
            )
            server_id = int(
                await connection.scalar(
                    servers.insert()
                    .values(
                        server_name=f"change03-server-{uuid4().hex}",
                        host="change03-vpn.example.test",
                        is_enabled=True,
                    )
                    .returning(servers.c.server_id)
                )
            )
            await connection.execute(
                server_endpoints.insert().values(
                    server_id=server_id,
                    protocol="vless",
                    port=443,
                    node_id=node_id,
                    local_inbound_id="main-vless",
                    is_enabled=True,
                )
            )

        yield _Harness(
            engine=engine,
            subscription_ids=(subscription_ids[0], subscription_ids[1]),
            now=now,
        )
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await admin_engine.dispose()


async def test_two_concurrent_provisioners_cannot_reserve_final_slot(
    change03_harness: _Harness,
) -> None:
    barrier = asyncio.Barrier(2)
    queue = _Queue()
    use_case = ProvisionVpn(
        uow_factory=lambda: _BarrierUow(change03_harness.engine, barrier),
        provisioning_service=VpnProvisioningService(VlessUriBuilder()),
        job_queue=queue,
        display_name_prefix="change03",
        node_inbound_id="main-vless",
        node_task_max_attempts=5,
        panel_task_max_attempts=5,
        xui_inbound_id=1,
        clock=lambda: change03_harness.now,
        node_health_stale_after_seconds=180,
    )

    first, second = await asyncio.wait_for(
        asyncio.gather(
            *(
                use_case.execute(subscription_id=value)
                for value in change03_harness.subscription_ids
            )
        ),
        timeout=15,
    )

    assert sorted((str(first["status"]), str(second["status"]))) == [
        "queued",
        "waiting_for_node_capacity",
    ]
    async with change03_harness.engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(vpn_configurations)) == 1
        assert await connection.scalar(select(func.count()).select_from(node_tasks)) == 1
        assert await connection.scalar(select(func.count()).select_from(audit_events)) == 1
        only_status = await connection.scalar(select(vpn_configurations.c.status))
        assert only_status == "provisioning"
    assert len(queue.jobs) == 1
