from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from shop_bot.domain.entities.node import NodeStatus


def _object_mapping(value: object, *, field_name: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return {str(key): item for key, item in value.items()}


def _optional_int(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer or null")
    return value


@dataclass(frozen=True, slots=True)
class NodeCapacitySnapshot:
    active_clients: int | None
    max_clients: int | None
    load: dict[str, object]
    traffic: dict[str, object]

    def as_metrics_payload(self) -> dict[str, object]:
        return {
            "active_clients": self.active_clients,
            "max_clients": self.max_clients,
            "load": dict(self.load),
            "traffic": dict(self.traffic),
        }


@dataclass(frozen=True, slots=True)
class NodeHealthSnapshot:
    health_status: NodeStatus
    agent_version: str | None
    capacity: NodeCapacitySnapshot
    capabilities: dict[str, object]
    status_payload: dict[str, object]
    inbounds: tuple[dict[str, object], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "NodeHealthSnapshot":
        health = _object_mapping(payload.get("health"), field_name="health")
        capabilities = _object_mapping(
            payload.get("capabilities"),
            field_name="capabilities",
        )
        status_payload = _object_mapping(payload.get("status"), field_name="status")
        metrics = _object_mapping(payload.get("metrics"), field_name="metrics")

        raw_status = status_payload.get("status") or NodeStatus.ONLINE
        try:
            health_status = NodeStatus(str(raw_status).lower())
        except ValueError:
            health_status = NodeStatus.UNKNOWN

        raw_agent_version = payload.get("agent_version") or health.get("agent_version")
        agent_version = str(raw_agent_version) if raw_agent_version else None

        active_clients = metrics.get("active_clients", status_payload.get("active_clients"))
        max_clients = metrics.get("max_clients", status_payload.get("max_clients"))
        load = _object_mapping(
            metrics.get("load", status_payload.get("load")),
            field_name="metrics.load",
        )
        traffic = _object_mapping(
            metrics.get("traffic", status_payload.get("traffic")),
            field_name="metrics.traffic",
        )

        raw_inbounds = payload.get("inbounds") or status_payload.get("inbounds", ())
        if isinstance(raw_inbounds, (str, bytes)) or not isinstance(raw_inbounds, Sequence):
            raise ValueError("inbounds must be an array")
        inbounds = tuple(
            _object_mapping(item, field_name="inbounds item") for item in raw_inbounds
        )

        return cls(
            health_status=health_status,
            agent_version=agent_version,
            capacity=NodeCapacitySnapshot(
                active_clients=_optional_int(
                    active_clients,
                    field_name="metrics.active_clients",
                ),
                max_clients=_optional_int(
                    max_clients,
                    field_name="metrics.max_clients",
                ),
                load=load,
                traffic=traffic,
            ),
            capabilities=capabilities,
            status_payload=status_payload,
            inbounds=inbounds,
        )
