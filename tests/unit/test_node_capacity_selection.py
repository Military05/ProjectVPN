from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from shop_bot.domain.services.node_selection import (
    NodeAvailabilityPolicy,
    NodeSelectionCandidate,
    WeightedNodeSelector,
)
from shop_bot.infrastructure.persistence.repositories.servers import ServerRepository


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def candidate(
    node_id: int,
    *,
    endpoint_id: int | None = None,
    status: str = "online",
    enabled: bool = True,
    checked_at: datetime | None = NOW,
    reported: int | None = 0,
    reserved: int = 0,
    maximum: int | None = 10,
    weight: int = 100,
) -> NodeSelectionCandidate:
    return NodeSelectionCandidate(
        node_id=node_id,
        endpoint_id=endpoint_id or node_id * 10,
        node_status=status,
        is_enabled=enabled,
        last_checked_at=checked_at,
        active_clients=reported,
        max_clients=maximum,
        selection_weight=weight,
        local_reserved_clients=reserved,
    )


@pytest.mark.parametrize(
    "item",
    [
        candidate(1, enabled=False),
        candidate(1, status="unknown"),
        candidate(1, status="offline"),
        candidate(1, checked_at=NOW - timedelta(seconds=181)),
        candidate(1, maximum=None),
        candidate(1, reported=10, maximum=10),
        candidate(1, reported=0, reserved=10, maximum=10),
    ],
)
def test_policy_fails_closed_for_unavailable_or_full_nodes(
    item: NodeSelectionCandidate,
) -> None:
    assert NodeAvailabilityPolicy(180).eligible(item, NOW) is False


def test_effective_capacity_uses_max_of_reported_and_local_reservations() -> None:
    local_wins = candidate(1, reported=2, reserved=7)
    reported_wins = candidate(2, reported=8, reserved=3)

    assert local_wins.effective_clients == 7
    assert reported_wins.effective_clients == 8
    assert NodeAvailabilityPolicy().eligible(local_wins, NOW)
    assert NodeAvailabilityPolicy().eligible(reported_wins, NOW)


def test_online_pool_is_preferred_before_degraded_pool() -> None:
    selector = WeightedNodeSelector(randbelow=lambda upper: upper - 1)
    selected = selector.select(
        [
            candidate(1, status="degraded", weight=10_000),
            candidate(2, status="online", weight=1),
        ],
        now=NOW,
        policy=NodeAvailabilityPolicy(),
    )

    assert selected is not None
    assert selected.node_id == 2


@pytest.mark.parametrize(
    ("ticket", "expected_node"),
    [(0, 1), (99, 1), (100, 2), (149, 2)],
)
def test_weighted_selection_has_deterministic_ticket_boundaries(
    ticket: int,
    expected_node: int,
) -> None:
    selector = WeightedNodeSelector(randbelow=lambda _upper: ticket)
    selected = selector.select(
        [candidate(1, weight=100), candidate(2, weight=50)],
        now=NOW,
        policy=NodeAvailabilityPolicy(),
    )

    assert selected is not None
    assert selected.node_id == expected_node


def test_multiple_endpoints_do_not_multiply_node_weight() -> None:
    selector = WeightedNodeSelector(randbelow=lambda _upper: 100)
    selected = selector.select(
        [
            candidate(1, endpoint_id=12, weight=100),
            candidate(1, endpoint_id=11, weight=100),
            candidate(2, endpoint_id=21, weight=50),
        ],
        now=NOW,
        policy=NodeAvailabilityPolicy(),
    )

    assert selected is not None
    assert selected.node_id == 2


def test_disabled_node_is_reachable_for_revoke_but_not_provision() -> None:
    policy = NodeAvailabilityPolicy()
    common = {
        "node_status": "online",
        "is_enabled": False,
        "last_checked_at": NOW,
        "now": NOW,
    }

    assert not policy.allows_operation(**common, require_enabled=True)
    assert policy.allows_operation(**common, require_enabled=False)


class _Mappings:
    def first(self) -> None:
        return None


class _Result:
    def mappings(self) -> _Mappings:
        return _Mappings()


class _Connection:
    def __init__(self) -> None:
        self.scalar_statements: list[Any] = []
        self.execute_statements: list[Any] = []

    async def scalar(self, statement: Any) -> int:
        self.scalar_statements.append(statement)
        return 7

    async def execute(self, statement: Any) -> _Result:
        self.execute_statements.append(statement)
        return _Result()


@pytest.mark.asyncio
async def test_revalidation_locks_node_before_capacity_snapshot() -> None:
    connection = _Connection()
    repository = ServerRepository(connection)  # type: ignore[arg-type]

    await repository.lock_and_revalidate_candidate(7, 11)

    assert len(connection.scalar_statements) == 1
    assert len(connection.execute_statements) == 1
    lock_sql = str(
        connection.scalar_statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    capacity_sql = str(
        connection.execute_statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "FOR UPDATE" in lock_sql
    assert (
        "vpn_configurations.status IN "
        "('provisioning', 'active', 'failed', 'revoking', 'revoke_failed')"
        in capacity_sql
    )
    assert "local_reserved_clients" in capacity_sql
    assert "server_endpoints.server_endpoint_id = 11" in capacity_sql
