from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, and_, case, select
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.infrastructure.db.tables import node_status, nodes, server_endpoints, servers


class ServerRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def create_server(self, server_name: str, host: str, is_enabled: bool = True) -> Mapping[str, Any]:
        result = await self.connection.execute(
            servers.insert().values(server_name=server_name, host=host, is_enabled=is_enabled)
        )
        server_id = int(result.scalar_one())
        return {
            "server_id": server_id,
            "server_name": server_name,
            "host": host,
            "is_enabled": is_enabled,
        }

    async def create_server_endpoint(self, **payload: Any) -> Mapping[str, Any]:
        result = await self.connection.execute(server_endpoints.insert().values(**payload))
        endpoint_id = int(result.scalar_one())
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
        online_rank = case(
            (node_status.c.health_status == "online", 0),
            (server_endpoints.c.node_id.is_(None), 1),
            else_=2,
        )
        query: Select[Any] = (
            select(
                server_endpoints,
                servers.c.server_name,
                servers.c.host,
                nodes.c.node_key,
                nodes.c.api_base_url,
                nodes.c.is_enabled.label("node_is_enabled"),
                node_status.c.health_status,
            )
            .join(servers, servers.c.server_id == server_endpoints.c.server_id)
            .outerjoin(nodes, nodes.c.node_id == server_endpoints.c.node_id)
            .outerjoin(node_status, node_status.c.node_id == server_endpoints.c.node_id)
            .where(
                and_(
                    servers.c.is_enabled.is_(True),
                    server_endpoints.c.is_enabled.is_(True),
                    (server_endpoints.c.node_id.is_(None) | nodes.c.is_enabled.is_(True)),
                )
            )
            .order_by(
                online_rank.asc(),
                nodes.c.selection_weight.desc().nullslast(),
                servers.c.server_id.asc(),
                server_endpoints.c.server_endpoint_id.asc(),
            )
            .limit(1)
        )
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
