from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.infrastructure.db.tables import (
    server_endpoints,
    servers,
    subscription_periods,
    subscriptions,
    vpn_configurations,
)


class VpnRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def get_active_configuration_for_subscription(
        self,
        subscription_id: int,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(vpn_configurations)
            .where(
                vpn_configurations.c.subscription_id == subscription_id,
                vpn_configurations.c.status == "active",
            )
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_latest_configuration_for_subscription(
        self,
        subscription_id: int,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(vpn_configurations)
            .where(vpn_configurations.c.subscription_id == subscription_id)
            .order_by(vpn_configurations.c.created_at.desc())
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def create_configuration(
        self,
        subscription_id: int,
        server_endpoint_id: int,
        client_uuid: UUID,
        display_name: str,
        status: str,
        *,
        remote_client_ref: str | None = None,
    ) -> int:
        statement = (
            vpn_configurations.insert()
            .values(
                subscription_id=subscription_id,
                server_endpoint_id=server_endpoint_id,
                client_uuid=client_uuid,
                display_name=display_name,
                status=status,
                remote_client_ref=remote_client_ref,
            )
            .returning(vpn_configurations.c.vpn_configuration_id)
        )

        result = await self.connection.execute(statement)
        return int(result.scalar_one())

    async def activate_configuration(
        self,
        vpn_configuration_id: int,
        *,
        remote_client_ref: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": "active",
            "revoked_at": None,
        }

        if remote_client_ref is not None:
            values["remote_client_ref"] = remote_client_ref

        await self.connection.execute(
            update(vpn_configurations)
            .where(vpn_configurations.c.vpn_configuration_id == vpn_configuration_id)
            .values(**values)
        )

    async def mark_configuration_status(
        self,
        vpn_configuration_id: int,
        *,
        status: str,
        revoked_at: datetime | None = None,
        remote_client_ref: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
        }

        if revoked_at is not None:
            values["revoked_at"] = revoked_at

        if remote_client_ref is not None:
            values["remote_client_ref"] = remote_client_ref

        await self.connection.execute(
            update(vpn_configurations)
            .where(vpn_configurations.c.vpn_configuration_id == vpn_configuration_id)
            .values(**values)
        )

    async def revoke_configuration(
        self,
        vpn_configuration_id: int,
        revoked_at: datetime,
        status: str = "revoked",
    ) -> None:
        await self.connection.execute(
            update(vpn_configurations)
            .where(vpn_configurations.c.vpn_configuration_id == vpn_configuration_id)
            .values(
                status=status,
                revoked_at=revoked_at,
            )
        )

    async def get_configuration_with_endpoint(
        self,
        vpn_configuration_id: int,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(
                vpn_configurations,
                server_endpoints,
                servers.c.host,
                servers.c.server_name,
            )
            .join(
                server_endpoints,
                server_endpoints.c.server_endpoint_id == vpn_configurations.c.server_endpoint_id,
            )
            .join(
                servers,
                servers.c.server_id == server_endpoints.c.server_id,
            )
            .where(vpn_configurations.c.vpn_configuration_id == vpn_configuration_id)
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_active_configuration_for_user(
        self,
        user_id: int,
        now: datetime,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(
                vpn_configurations,
                server_endpoints,
                servers.c.host,
                servers.c.server_name,
            )
            .join(
                subscriptions,
                subscriptions.c.subscription_id == vpn_configurations.c.subscription_id,
            )
            .join(
                subscription_periods,
                subscription_periods.c.subscription_id == subscriptions.c.subscription_id,
            )
            .join(
                server_endpoints,
                server_endpoints.c.server_endpoint_id == vpn_configurations.c.server_endpoint_id,
            )
            .join(
                servers,
                servers.c.server_id == server_endpoints.c.server_id,
            )
            .where(
                subscriptions.c.user_id == user_id,
                subscriptions.c.status == "active",
                subscription_periods.c.is_paid.is_(True),
                subscription_periods.c.starts_at <= now,
                subscription_periods.c.expires_at > now,
                vpn_configurations.c.status == "active",
            )
            .order_by(vpn_configurations.c.created_at.desc())
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_active_configuration_by_client_uuid(
        self,
        client_uuid: UUID,
        now: datetime,
    ) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(
                vpn_configurations,
                server_endpoints,
                servers.c.host,
                servers.c.server_name,
                subscriptions.c.user_id,
                subscriptions.c.tariff_id,
                subscriptions.c.status.label("subscription_status"),
            )
            .join(
                subscriptions,
                subscriptions.c.subscription_id == vpn_configurations.c.subscription_id,
            )
            .join(
                subscription_periods,
                subscription_periods.c.subscription_id == subscriptions.c.subscription_id,
            )
            .join(
                server_endpoints,
                server_endpoints.c.server_endpoint_id == vpn_configurations.c.server_endpoint_id,
            )
            .join(
                servers,
                servers.c.server_id == server_endpoints.c.server_id,
            )
            .where(
                vpn_configurations.c.client_uuid == client_uuid,
                vpn_configurations.c.status == "active",
                subscriptions.c.status == "active",
                subscription_periods.c.is_paid.is_(True),
                subscription_periods.c.starts_at <= now,
                subscription_periods.c.expires_at > now,
                servers.c.is_enabled.is_(True),
                server_endpoints.c.is_enabled.is_(True),
            )
            .order_by(subscription_periods.c.expires_at.desc())
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def list_configurations(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Mapping[str, Any]]:
        query: Select[Any] = (
            select(
                vpn_configurations,
                servers.c.server_name,
                servers.c.host,
            )
            .join(
                server_endpoints,
                server_endpoints.c.server_endpoint_id == vpn_configurations.c.server_endpoint_id,
            )
            .join(
                servers,
                servers.c.server_id == server_endpoints.c.server_id,
            )
            .order_by(vpn_configurations.c.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.connection.execute(query)
        return list(result.mappings().all())

    async def list_active_configuration_ids_due_for_revoke(
        self,
        now: datetime,
    ) -> list[int]:
        query: Select[Any] = (
            select(vpn_configurations.c.vpn_configuration_id)
            .join(
                subscriptions,
                subscriptions.c.subscription_id == vpn_configurations.c.subscription_id,
            )
            .where(vpn_configurations.c.status == "active")
            .where(
                ~subscriptions.c.subscription_id.in_(
                    select(subscription_periods.c.subscription_id).where(
                        subscription_periods.c.is_paid.is_(True),
                        subscription_periods.c.starts_at <= now,
                        subscription_periods.c.expires_at > now,
                    )
                )
            )
        )
        result = await self.connection.execute(query)
        return [int(value) for value in result.scalars().all()]