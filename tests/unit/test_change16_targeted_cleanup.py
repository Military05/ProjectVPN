from __future__ import annotations

from dataclasses import is_dataclass
from datetime import UTC, datetime
from typing import Any, get_type_hints

import pytest

from shop_bot.application.ports import NodeGateway
from shop_bot.application.use_cases.sync_nodes import SyncNodeStatus
from shop_bot.domain.entities.node import NodeStatus
from shop_bot.domain.node_health import NodeCapacitySnapshot, NodeHealthSnapshot
from shop_bot.infrastructure.nodes.client import NodeApiClient


NOW = datetime(2026, 9, 15, 15, 0, tzinfo=UTC)


def snapshot_payload(*, status: str = "degraded") -> dict[str, object]:
    inbound = {
        "local_inbound_id": "main",
        "port": 443,
        "protocol": "vless",
    }
    return {
        "health": {"status": "ok", "agent_version": "1.2.3"},
        "capabilities": {"supports": {"provision_client": True}},
        "status": {
            "status": status,
            "active_clients": 7,
            "max_clients": 20,
            "load": {"cpu_percent": 12.5},
            "traffic": {"rx_bytes": 10, "tx_bytes": 20},
            "inbounds": [inbound],
        },
    }


def test_node_health_boundary_is_a_frozen_typed_dataclass() -> None:
    assert is_dataclass(NodeCapacitySnapshot)
    assert is_dataclass(NodeHealthSnapshot)
    assert NodeCapacitySnapshot.__dataclass_params__.frozen is True
    assert NodeHealthSnapshot.__dataclass_params__.frozen is True
    assert get_type_hints(NodeGateway.get_snapshot)["return"] is NodeHealthSnapshot
    assert get_type_hints(NodeApiClient.get_snapshot)["return"] is NodeHealthSnapshot

    snapshot = NodeHealthSnapshot.from_payload(snapshot_payload())

    assert snapshot.health_status is NodeStatus.DEGRADED
    assert snapshot.agent_version == "1.2.3"
    assert snapshot.capacity.active_clients == 7
    assert snapshot.capacity.max_clients == 20
    assert snapshot.capacity.as_metrics_payload() == {
        "active_clients": 7,
        "max_clients": 20,
        "load": {"cpu_percent": 12.5},
        "traffic": {"rx_bytes": 10, "tx_bytes": 20},
    }
    assert snapshot.inbounds[0]["local_inbound_id"] == "main"


def test_unknown_node_health_status_fails_closed_and_capacity_is_validated() -> None:
    snapshot = NodeHealthSnapshot.from_payload(snapshot_payload(status="invented"))
    assert snapshot.health_status is NodeStatus.UNKNOWN

    payload = snapshot_payload()
    status_payload = dict(payload["status"])  # type: ignore[arg-type]
    status_payload["active_clients"] = "seven"
    payload["status"] = status_payload
    with pytest.raises(ValueError, match="metrics.active_clients"):
        NodeHealthSnapshot.from_payload(payload)


class SnapshotClient(NodeApiClient):
    async def _request(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return snapshot_payload()  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_node_api_client_converts_wire_payload_at_the_adapter_boundary() -> None:
    client = SnapshotClient.__new__(SnapshotClient)

    snapshot = await client.get_snapshot(
        node={"node_id": 1},
        credential={"key_id": "default", "shared_secret": "secret"},
    )

    assert isinstance(snapshot, NodeHealthSnapshot)
    assert snapshot.health_status is NodeStatus.DEGRADED


class Nodes:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    async def get_node(self, node_id: int) -> dict[str, object]:
        return {"node_id": node_id, "node_key": "node-1", "api_base_url": "https://node.test"}

    async def get_active_credential(self, node_id: int) -> dict[str, object]:
        return {"node_id": node_id, "key_id": "default", "shared_secret": "secret"}

    async def upsert_node_status(self, **values: Any) -> None:
        self.writes.append(values)


class Uow:
    def __init__(self, nodes: Nodes) -> None:
        self.nodes = nodes

    async def __aenter__(self) -> "Uow":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def commit(self) -> None:
        return None


class Gateway:
    async def get_snapshot(self, **kwargs: Any) -> NodeHealthSnapshot:
        del kwargs
        return NodeHealthSnapshot.from_payload(snapshot_payload())


@pytest.mark.asyncio
async def test_sync_node_status_consumes_typed_snapshot_fields() -> None:
    nodes = Nodes()
    use_case = SyncNodeStatus(
        uow_factory=lambda: Uow(nodes),  # type: ignore[arg-type]
        node_gateway=Gateway(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    result = await use_case.execute(node_id=1)

    assert result == {"status": "ok", "synced": 1, "offline": 0}
    assert nodes.writes == [
        {
            "node_id": 1,
            "health_status": NodeStatus.DEGRADED.value,
            "agent_version": "1.2.3",
            "capabilities": {"supports": {"provision_client": True}},
            "metrics": {
                "active_clients": 7,
                "max_clients": 20,
                "load": {"cpu_percent": 12.5},
                "traffic": {"rx_bytes": 10, "tx_bytes": 20},
            },
            "status_payload": snapshot_payload()["status"],
            "inbounds": [
                {
                    "local_inbound_id": "main",
                    "port": 443,
                    "protocol": "vless",
                }
            ],
            "last_seen_at": NOW,
            "last_checked_at": NOW,
        }
    ]
