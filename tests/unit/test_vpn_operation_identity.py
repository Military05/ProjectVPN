from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from shop_bot.domain.entities.panel_task import PanelRevokeTask, PanelRevokeTaskStatus
from shop_bot.infrastructure.persistence.repositories.nodes import NodeRepository
from shop_bot.infrastructure.persistence.repositories.vpn import VpnRepository
from shop_bot.infrastructure.persistence.sqlalchemy.tables import node_tasks, panel_revoke_tasks


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


class EmptyMappingsResult:
    def mappings(self) -> EmptyMappingsResult:
        return self

    def first(self) -> None:
        return None


class RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> EmptyMappingsResult:
        self.statements.append(statement)
        return EmptyMappingsResult()


def compiled_sql(statement: Any) -> str:
    return " ".join(str(statement.compile(dialect=postgresql.dialect())).split())


def test_physical_operation_indexes_match_change04_identity() -> None:
    node_index = next(
        index
        for index in node_tasks.indexes
        if index.name == "uq_node_tasks_vpn_operation_generation"
    )
    assert node_index.unique is True
    assert [column.name for column in node_index.columns] == [
        "vpn_configuration_id",
        "operation",
        "vpn_generation",
    ]
    assert str(node_index.dialect_options["postgresql"]["where"]) == (
        "vpn_configuration_id IS NOT NULL"
    )

    panel_index = next(
        index
        for index in panel_revoke_tasks.indexes
        if index.name == "uq_panel_revoke_tasks_vpn_generation"
    )
    assert panel_index.unique is True
    assert [column.name for column in panel_index.columns] == [
        "vpn_configuration_id",
        "vpn_generation",
    ]


@pytest.mark.asyncio
async def test_node_physical_conflict_returns_winning_task_even_with_different_key() -> None:
    connection = RecordingConnection()
    repository = NodeRepository(connection)  # type: ignore[arg-type]
    winning_row = {"node_task_id": 91, "idempotency_key": "winner"}
    repository.get_task_by_idempotency = AsyncMock(return_value=None)  # type: ignore[method-assign]
    repository._get_vpn_task_row_for_generation = AsyncMock(  # type: ignore[method-assign]
        return_value=winning_row
    )

    row = await repository.create_task(
        node_id=5,
        operation="provision_client",
        idempotency_key="loser",
        payload={"client_uuid": "client-1"},
        vpn_configuration_id=7,
        subscription_id=1,
        vpn_generation=3,
        task_uuid=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        next_retry_at=NOW,
    )

    assert row is winning_row
    assert "ON CONFLICT DO NOTHING" in compiled_sql(connection.statements[0])
    repository.get_task_by_idempotency.assert_awaited_once_with("loser")
    repository._get_vpn_task_row_for_generation.assert_awaited_once_with(  # type: ignore[attr-defined]
        7,
        "provision_client",
        3,
    )


@pytest.mark.asyncio
async def test_panel_revoke_physical_conflict_returns_generation_owner() -> None:
    connection = RecordingConnection()
    repository = VpnRepository(connection)  # type: ignore[arg-type]
    winning_task = PanelRevokeTask(
        id=81,
        task_uuid=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        vpn_configuration_id=7,
        subscription_id=1,
        vpn_generation=3,
        status=PanelRevokeTaskStatus.PENDING,
        idempotency_key="panel-winner",
        payload={"client_uuid": "client-1", "xui_inbound_id": 1},
        next_retry_at=NOW,
    )
    repository.get_panel_revoke_task_for_generation = AsyncMock(  # type: ignore[method-assign]
        return_value=winning_task
    )
    losing_task = PanelRevokeTask(
        id=None,
        task_uuid=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        vpn_configuration_id=7,
        subscription_id=1,
        vpn_generation=3,
        status=PanelRevokeTaskStatus.PENDING,
        idempotency_key="panel-loser",
        payload={"client_uuid": "client-1", "xui_inbound_id": 1},
        next_retry_at=NOW,
    )

    persisted = await repository.add_panel_revoke_task_entity(losing_task)

    assert persisted is winning_task
    assert (
        "ON CONFLICT (vpn_configuration_id, vpn_generation) DO NOTHING"
        in compiled_sql(connection.statements[0])
    )
    repository.get_panel_revoke_task_for_generation.assert_awaited_once_with(  # type: ignore[attr-defined]
        7,
        3,
    )


@pytest.mark.asyncio
async def test_generation_lookup_is_status_agnostic_and_includes_terminal_tasks() -> None:
    connection = RecordingConnection()
    repository = NodeRepository(connection)  # type: ignore[arg-type]

    task = await repository.get_vpn_task_for_generation(7, "provision_client", 3)

    assert task is None
    sql = compiled_sql(connection.statements[0])
    assert "vpn_configuration_id" in sql
    assert "operation" in sql
    assert "vpn_generation" in sql
    assert "status" not in sql.split(" WHERE ", 1)[1]
