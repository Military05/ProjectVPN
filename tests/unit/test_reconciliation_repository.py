from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from shop_bot.domain.reconciliation import ReconciliationAnomalyKind
from shop_bot.infrastructure.persistence.repositories.maintenance import MaintenanceRepository


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
OWNER_TOKEN = UUID("11111111-1111-4111-8111-111111111111")


class Result:
    def __init__(
        self,
        *,
        scalar: Any = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.scalar = scalar
        self.rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self.scalar

    def mappings(self) -> "Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class Connection:
    def __init__(self, *results: Result) -> None:
        self.results = list(results)
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Result:
        self.statements.append(statement)
        return self.results.pop(0) if self.results else Result()


def sql(statement: Any, *, literals: bool = False) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": literals},
        )
    )


@pytest.mark.asyncio
async def test_lease_acquire_is_atomic_and_renew_release_are_token_fenced() -> None:
    connection = Connection(Result(scalar=OWNER_TOKEN), Result(scalar=OWNER_TOKEN), Result(scalar=OWNER_TOKEN))
    repository = MaintenanceRepository(connection)  # type: ignore[arg-type]

    assert await repository.acquire_lease(
        lease_name="reconcile_subscriptions",
        owner_token=OWNER_TOKEN,
        now=NOW,
        lease_seconds=120,
    )
    assert await repository.renew_lease(
        lease_name="reconcile_subscriptions",
        owner_token=OWNER_TOKEN,
        now=NOW,
        lease_seconds=120,
    )
    assert await repository.release_lease(
        lease_name="reconcile_subscriptions",
        owner_token=OWNER_TOKEN,
    )

    acquire_sql, renew_sql, release_sql = map(sql, connection.statements)
    assert "ON CONFLICT (lease_name) DO UPDATE" in acquire_sql
    assert "maintenance_leases.lease_expires_at <=" in acquire_sql
    assert "RETURNING maintenance_leases.owner_token" in acquire_sql
    assert "maintenance_leases.owner_token =" in renew_sql
    assert "maintenance_leases.owner_token =" in release_sql


@pytest.mark.asyncio
async def test_anomaly_query_is_one_keyset_limited_union_without_offset() -> None:
    connection = Connection(Result())
    repository = MaintenanceRepository(connection)  # type: ignore[arg-type]

    assert await repository.list_reconciliation_anomalies(
        now=NOW,
        after_kind_order=4,
        after_entity_id=99,
        limit=500,
    ) == []

    query_sql = sql(connection.statements[0], literals=True)
    assert query_sql.count("UNION ALL") == 5
    assert "reconciliation_anomalies.kind_order > 4" in query_sql
    assert "reconciliation_anomalies.entity_id > 99" in query_sql
    assert "ORDER BY reconciliation_anomalies.kind_order ASC" in query_sql
    assert "LIMIT 500" in query_sql
    assert "OFFSET" not in query_sql
    assert "node_tasks.status IN ('succeeded', 'failed', 'cancelled')" in query_sql
    assert "panel_provision_tasks.status IN ('succeeded', 'failed', 'cancelled')" in query_sql
    assert "node_tasks.status IN ('pending', 'in_progress')" not in query_sql


@pytest.mark.asyncio
async def test_anomaly_rows_are_mapped_to_typed_domain_records() -> None:
    connection = Connection(
        Result(
            rows=[
                {
                    "kind_order": 5,
                    "kind": "repair_provision",
                    "entity_id": 42,
                }
            ]
        )
    )
    repository = MaintenanceRepository(connection)  # type: ignore[arg-type]

    rows = await repository.list_reconciliation_anomalies(
        now=NOW,
        after_kind_order=0,
        after_entity_id=0,
        limit=1,
    )

    assert rows[0].kind is ReconciliationAnomalyKind.REPAIR_PROVISION
    assert rows[0].kind_order == 5
    assert rows[0].entity_id == 42
