from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from shop_bot.core.exceptions import ConflictError
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.infrastructure.persistence.sqlalchemy.tables import (
    node_status,
    nodes,
    server_endpoints,
    servers,
    vpn_configurations,
)


_CAPACITY_RESERVING_STATUSES = (
    "provisioning",
    "active",
    "failed",
    "revoking",
    "revoke_failed",
)


class ServerRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def create_server(
        self,
        server_name: str,
        host: str,
        is_enabled: bool = True,
    ) -> Mapping[str, Any]:
        # Both the name and host are independent conflict keys, so this statement
        # intentionally has no single conflict target. CHECK/FK violations are
        # still raised by PostgreSQL and are not converted into domain conflicts.
        result = await self.connection.execute(
            pg_insert(servers)
            .values(server_name=server_name, host=host, is_enabled=is_enabled)
            .on_conflict_do_nothing()
            .returning(servers.c.server_id)
        )
        row = result.first()
        if row is None:
            raise ConflictError("Server with the same name or host already exists")
        server_id = int(row[0])
        return {
            "server_id": server_id,
            "server_name": server_name,
            "host": host,
            "is_enabled": is_enabled,
        }

    async def create_server_endpoint(self, **payload: Any) -> Mapping[str, Any]:
        result = await self.connection.execute(
            pg_insert(server_endpoints)
            .values(**payload)
            .on_conflict_do_nothing(constraint="uq_server_endpoints_full_tuple")
            .returning(server_endpoints.c.server_endpoint_id)
        )
        row = result.first()
        if row is None:
            raise ConflictError("Server endpoint already exists")
        endpoint_id = int(row[0])
        created = await self.get_endpoint_with_server(endpoint_id)
        if created is None:
            raise RuntimeError("Failed to fetch created endpoint")
        return created

    async def list_servers(self) -> list[Mapping[str, Any]]:
        result = await self.connection.execute(select(servers).order_by(servers.c.server_id.asc()))
        return list(result.mappings().all())

    async def list_server_endpoints(self) -> list[Mapping[str, Any]]:
        query: Select[Any] = (
            select(
                server_endpoints,
                servers.c.server_name,
                servers.c.host,
                nodes.c.node_key,
                nodes.c.api_base_url,
                node_status.c.health_status,
            )
            .join(servers, servers.c.server_id == server_endpoints.c.server_id)
            .outerjoin(nodes, nodes.c.node_id == server_endpoints.c.node_id)
            .outerjoin(node_status, node_status.c.node_id == server_endpoints.c.node_id)
            .order_by(server_endpoints.c.server_endpoint_id.asc())
        )
        result = await self.connection.execute(query)
        return list(result.mappings().all())

    async def get_first_enabled_endpoint(self) -> Mapping[str, Any] | None:
        """Return a legacy panel endpoint without applying Node availability policy."""
        query: Select[Any] = (
            select(
                server_endpoints,
                servers.c.server_name,
                servers.c.host,
            )
            .join(servers, servers.c.server_id == server_endpoints.c.server_id)
            .where(
                and_(
                    servers.c.is_enabled.is_(True),
                    server_endpoints.c.is_enabled.is_(True),
                    server_endpoints.c.node_id.is_(None),
                )
            )
            .order_by(servers.c.server_id.asc(), server_endpoints.c.server_endpoint_id.asc())
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    @staticmethod
    def _node_candidate_query() -> Select[Any]:
        reserved = (
            select(
                server_endpoints.c.node_id.label("reserved_node_id"),
                func.count(vpn_configurations.c.vpn_configuration_id).label(
                    "local_reserved_clients"
                ),
            )
            .select_from(
                vpn_configurations.join(
                    server_endpoints,
                    server_endpoints.c.server_endpoint_id
                    == vpn_configurations.c.server_endpoint_id,
                )
            )
            .where(
                server_endpoints.c.node_id.is_not(None),
                vpn_configurations.c.status.in_(_CAPACITY_RESERVING_STATUSES),
            )
            .group_by(server_endpoints.c.node_id)
            .subquery("node_capacity_reservations")
        )
        return (
            select(
                server_endpoints,
                servers.c.server_name,
                servers.c.host,
                nodes.c.node_key,
                nodes.c.api_base_url,
                nodes.c.is_enabled.label("node_is_enabled"),
                nodes.c.selection_weight,
                node_status.c.health_status,
                node_status.c.last_checked_at,
                node_status.c.active_clients,
                node_status.c.max_clients,
                func.coalesce(reserved.c.local_reserved_clients, 0).label(
                    "local_reserved_clients"
                ),
            )
            .join(servers, servers.c.server_id == server_endpoints.c.server_id)
            .join(nodes, nodes.c.node_id == server_endpoints.c.node_id)
            .outerjoin(node_status, node_status.c.node_id == nodes.c.node_id)
            .outerjoin(reserved, reserved.c.reserved_node_id == nodes.c.node_id)
            .where(
                servers.c.is_enabled.is_(True),
                server_endpoints.c.is_enabled.is_(True),
            )
        )

    async def list_node_selection_candidates(
        self,
        *,
        now: datetime | None = None,
        stale_after_seconds: int = 180,
    ) -> list[Mapping[str, Any]]:
        # Kept as compatibility-only arguments for callers from a rolling deploy;
        # temporal availability belongs to NodeAvailabilityPolicy, not SQL.
        del now, stale_after_seconds
        query = self._node_candidate_query().order_by(
            nodes.c.node_id.asc(),
            server_endpoints.c.server_endpoint_id.asc(),
        )
        result = await self.connection.execute(query)
        return list(result.mappings().all())

    async def lock_and_revalidate_candidate(
        self,
        node_id: int,
        endpoint_id: int | None = None,
        *,
        now: datetime | None = None,
        stale_after_seconds: int = 180,
    ) -> Mapping[str, Any] | None:
        del now, stale_after_seconds
        # Lock first, then issue a second statement. Under PostgreSQL READ COMMITTED,
        # this guarantees the capacity query sees reservations committed while this
        # transaction was waiting for the node-row lock.
        locked_node_id = await self.connection.scalar(
            select(nodes.c.node_id)
            .where(nodes.c.node_id == node_id)
            .with_for_update()
        )
        if locked_node_id is None:
            return None
        query = self._node_candidate_query().where(nodes.c.node_id == node_id)
        if endpoint_id is not None:
            query = query.where(server_endpoints.c.server_endpoint_id == endpoint_id)
        query = query.order_by(server_endpoints.c.server_endpoint_id.asc()).limit(1)
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_endpoint_with_server(self, server_endpoint_id: int) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(
                server_endpoints,
                servers.c.server_name,
                servers.c.host,
                nodes.c.node_key,
                nodes.c.api_base_url,
                node_status.c.health_status,
            )
            .join(servers, servers.c.server_id == server_endpoints.c.server_id)
            .outerjoin(nodes, nodes.c.node_id == server_endpoints.c.node_id)
            .outerjoin(node_status, node_status.c.node_id == server_endpoints.c.node_id)
            .where(server_endpoints.c.server_endpoint_id == server_endpoint_id)
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()
