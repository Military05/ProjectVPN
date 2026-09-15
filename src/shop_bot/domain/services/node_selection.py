from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import secrets
from typing import Callable, Iterable

from shop_bot.domain.entities.node import NodeStatus


@dataclass(frozen=True, slots=True)
class NodeSelectionCandidate:
    node_id: int
    endpoint_id: int
    node_status: NodeStatus | str
    is_enabled: bool
    last_checked_at: datetime | None
    active_clients: int | None
    max_clients: int | None
    selection_weight: int
    local_reserved_clients: int = 0

    @property
    def effective_clients(self) -> int:
        return max(
            max(self.active_clients or 0, 0),
            max(self.local_reserved_clients, 0),
        )


@dataclass(frozen=True, slots=True)
class NodeAvailabilityPolicy:
    stale_after_seconds: int = 180

    def allows_operation(
        self,
        *,
        node_status: NodeStatus | str,
        is_enabled: bool,
        last_checked_at: datetime | None,
        now: datetime,
        require_enabled: bool,
    ) -> bool:
        if require_enabled and not is_enabled:
            return False
        if last_checked_at is None or last_checked_at < now - timedelta(seconds=self.stale_after_seconds):
            return False
        try:
            status = NodeStatus(node_status)
        except ValueError:
            return False
        return status in {NodeStatus.ONLINE, NodeStatus.DEGRADED}

    def eligible(self, candidate: NodeSelectionCandidate, now: datetime) -> bool:
        if candidate.max_clients is None or candidate.max_clients <= 0:
            return False
        if not self.allows_operation(
            node_status=candidate.node_status,
            is_enabled=candidate.is_enabled,
            last_checked_at=candidate.last_checked_at,
            now=now,
            require_enabled=True,
        ):
            return False
        return candidate.effective_clients < candidate.max_clients


class WeightedNodeSelector:
    def __init__(self, *, randbelow: Callable[[int], int] = secrets.randbelow) -> None:
        self._randbelow = randbelow

    def select(self, candidates: Iterable[NodeSelectionCandidate], *, now: datetime, policy: NodeAvailabilityPolicy) -> NodeSelectionCandidate | None:
        eligible = [c for c in candidates if policy.eligible(c, now)]
        online = [c for c in eligible if NodeStatus(c.node_status) is NodeStatus.ONLINE]
        pool = online or [c for c in eligible if NodeStatus(c.node_status) is NodeStatus.DEGRADED]
        if not pool:
            return None
        # one weighted node per node id; endpoint tie-break is deterministic
        by_node: dict[int, NodeSelectionCandidate] = {}
        for candidate in sorted(pool, key=lambda c: (c.node_id, c.endpoint_id)):
            by_node.setdefault(candidate.node_id, candidate)
        nodes = list(by_node.values())
        total = sum(max(1, int(c.selection_weight)) for c in nodes)
        ticket = self._randbelow(total)
        cumulative = 0
        for candidate in nodes:
            cumulative += max(1, int(candidate.selection_weight))
            if ticket < cumulative:
                return candidate
        return nodes[-1]
