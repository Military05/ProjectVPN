from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.domain.entities.panel_task import PanelProvisionTask, PanelRevokeTask
from shop_bot.domain.entities.vpn import VpnConfiguration
from shop_bot.infrastructure.persistence.sqlalchemy.mappers import (
    panel_provision_task_from_row,
    panel_revoke_task_from_row,
    vpn_configuration_from_row,
)
from shop_bot.infrastructure.persistence.sqlalchemy.tables import (
    server_endpoints,
    servers,
    subscription_periods,
    subscriptions,
    panel_provision_tasks,
    panel_revoke_tasks,
    vpn_configurations,
)


class VpnRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def get_active_configuration_for_subscription(self, subscription_id: int) -> Mapping[str, Any] | None:
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

    async def get_latest_configuration_for_subscription(self, subscription_id: int) -> Mapping[str, Any] | None:
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
        desired_state: str = "active",
        generation: int = 1,
        remote_client_ref: str | None = None,
    ) -> int:
        result = await self.connection.execute(
            vpn_configurations.insert()
            .values(
                subscription_id=subscription_id,
                server_endpoint_id=server_endpoint_id,
                client_uuid=client_uuid,
                display_name=display_name,
                status=status,
                desired_state=desired_state,
                generation=generation,
                remote_client_ref=remote_client_ref,
            )
            .returning(vpn_configurations.c.vpn_configuration_id)
        )
        return int(result.scalar_one())

    async def activate_configuration(
        self,
        vpn_configuration_id: int,
        *,
        remote_client_ref: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": "active", "revoked_at": None}
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
        values: dict[str, Any] = {"status": status}
        if revoked_at is not None:
            values["revoked_at"] = revoked_at
        if remote_client_ref is not None:
            values["remote_client_ref"] = remote_client_ref
        await self.connection.execute(
            update(vpn_configurations)
            .where(vpn_configurations.c.vpn_configuration_id == vpn_configuration_id)
            .values(**values)
        )

    async def revoke_configuration(self, vpn_configuration_id: int, revoked_at: datetime, status: str = "revoked") -> None:
        await self.connection.execute(
            update(vpn_configurations)
            .where(vpn_configurations.c.vpn_configuration_id == vpn_configuration_id)
            .values(status=status, revoked_at=revoked_at)
        )

    async def get_configuration_with_endpoint(self, vpn_configuration_id: int) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(vpn_configurations, server_endpoints, servers.c.host, servers.c.server_name)
            .join(server_endpoints, server_endpoints.c.server_endpoint_id == vpn_configurations.c.server_endpoint_id)
            .join(servers, servers.c.server_id == server_endpoints.c.server_id)
            .where(vpn_configurations.c.vpn_configuration_id == vpn_configuration_id)
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_active_configuration_for_user(self, user_id: int, now: datetime) -> Mapping[str, Any] | None:
        query: Select[Any] = (
            select(vpn_configurations, server_endpoints, servers.c.host, servers.c.server_name)
            .join(subscriptions, subscriptions.c.subscription_id == vpn_configurations.c.subscription_id)
            .join(subscription_periods, subscription_periods.c.subscription_id == subscriptions.c.subscription_id)
            .join(server_endpoints, server_endpoints.c.server_endpoint_id == vpn_configurations.c.server_endpoint_id)
            .join(servers, servers.c.server_id == server_endpoints.c.server_id)
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

    async def list_configurations(self, limit: int = 100, offset: int = 0) -> list[Mapping[str, Any]]:
        query: Select[Any] = (
            select(
                vpn_configurations.c.vpn_configuration_id,
                vpn_configurations.c.subscription_id,
                vpn_configurations.c.server_endpoint_id,
                vpn_configurations.c.client_uuid,
                vpn_configurations.c.display_name,
                vpn_configurations.c.status,
                vpn_configurations.c.remote_client_ref,
                vpn_configurations.c.created_at,
                vpn_configurations.c.revoked_at,
                servers.c.server_name,
                servers.c.host,
            )
            .join(server_endpoints, server_endpoints.c.server_endpoint_id == vpn_configurations.c.server_endpoint_id)
            .join(servers, servers.c.server_id == server_endpoints.c.server_id)
            .order_by(vpn_configurations.c.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.connection.execute(query)
        return list(result.mappings().all())

    async def get_panel_task_entity(
        self, panel_task_id: int, *, for_update: bool = False
    ) -> PanelProvisionTask | None:
        query: Select[Any] = (
            select(panel_provision_tasks)
            .where(panel_provision_tasks.c.panel_provision_task_id == panel_task_id)
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        result = await self.connection.execute(query)
        row = result.mappings().first()
        return panel_provision_task_from_row(row) if row is not None else None

    async def get_panel_task_for_configuration(
        self, vpn_configuration_id: int
    ) -> PanelProvisionTask | None:
        result = await self.connection.execute(
            select(panel_provision_tasks)
            .where(panel_provision_tasks.c.vpn_configuration_id == vpn_configuration_id)
            .limit(1)
        )
        row = result.mappings().first()
        return panel_provision_task_from_row(row) if row is not None else None

    async def add_panel_task_entity(self, task: PanelProvisionTask) -> PanelProvisionTask:
        statement = (
            pg_insert(panel_provision_tasks)
            .values(
                vpn_configuration_id=task.vpn_configuration_id,
                subscription_id=task.subscription_id,
                status=str(task.status),
                idempotency_key=task.idempotency_key,
                payload=task.payload,
                vpn_generation=task.vpn_generation,
                attempts=task.attempts,
                max_attempts=task.max_attempts,
                next_retry_at=task.next_retry_at,
                last_error=task.last_error,
                claimed_at=task.claimed_at,
                lease_expires_at=task.lease_expires_at,
                lease_token=task.lease_token,
                compensation_required=task.compensation_required,
                completed_at=task.completed_at,
            )
            .on_conflict_do_nothing(index_elements=[panel_provision_tasks.c.vpn_configuration_id])
            .returning(panel_provision_tasks)
        )
        result = await self.connection.execute(statement)
        row = result.mappings().first()
        if row is None:
            existing = await self.get_panel_task_for_configuration(task.vpn_configuration_id)
            if existing is None:
                raise RuntimeError("Failed to persist panel provision task")
            return existing
        return panel_provision_task_from_row(row)

    async def save_panel_task_entity(self, task: PanelProvisionTask) -> None:
        if task.id is None:
            raise ValueError("Cannot save panel provision task without id")
        await self.connection.execute(
            update(panel_provision_tasks)
            .where(panel_provision_tasks.c.panel_provision_task_id == task.id)
            .values(
                status=str(task.status),
                payload=task.payload,
                vpn_generation=task.vpn_generation,
                attempts=task.attempts,
                max_attempts=task.max_attempts,
                next_retry_at=task.next_retry_at,
                last_error=task.last_error,
                claimed_at=task.claimed_at,
                lease_expires_at=task.lease_expires_at,
                lease_token=task.lease_token,
                compensation_required=task.compensation_required,
                updated_at=task.updated_at,
                completed_at=task.completed_at,
            )
        )

    async def list_due_panel_task_ids(self, now: datetime, limit: int = 100) -> list[int]:
        result = await self.connection.execute(
            select(panel_provision_tasks.c.panel_provision_task_id)
            .where(
                panel_provision_tasks.c.status == "pending",
                panel_provision_tasks.c.next_retry_at <= now,
            )
            .order_by(panel_provision_tasks.c.next_retry_at.asc())
            .limit(limit)
        )
        return [int(value) for value in result.scalars().all()]

    async def list_stale_panel_task_entities(
        self, now: datetime, limit: int = 100
    ) -> list[PanelProvisionTask]:
        query: Select[Any] = (
            select(panel_provision_tasks)
            .where(
                panel_provision_tasks.c.status == "in_progress",
                panel_provision_tasks.c.lease_expires_at.is_not(None),
                panel_provision_tasks.c.lease_expires_at <= now,
            )
            .order_by(panel_provision_tasks.c.lease_expires_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        result = await self.connection.execute(query)
        return [panel_provision_task_from_row(row) for row in result.mappings().all()]

    async def get_panel_revoke_task_entity(
        self, panel_revoke_task_id: int, *, for_update: bool = False
    ) -> PanelRevokeTask | None:
        query: Select[Any] = (
            select(panel_revoke_tasks)
            .where(panel_revoke_tasks.c.panel_revoke_task_id == panel_revoke_task_id)
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        result = await self.connection.execute(query)
        row = result.mappings().first()
        return panel_revoke_task_from_row(row) if row is not None else None

    async def get_panel_revoke_task_for_generation(
        self, vpn_configuration_id: int, vpn_generation: int
    ) -> PanelRevokeTask | None:
        result = await self.connection.execute(
            select(panel_revoke_tasks)
            .where(
                panel_revoke_tasks.c.vpn_configuration_id == vpn_configuration_id,
                panel_revoke_tasks.c.vpn_generation == vpn_generation,
            )
            .order_by(panel_revoke_tasks.c.panel_revoke_task_id.desc())
            .limit(1)
        )
        row = result.mappings().first()
        return panel_revoke_task_from_row(row) if row is not None else None

    async def add_panel_revoke_task_entity(self, task: PanelRevokeTask) -> PanelRevokeTask:
        result = await self.connection.execute(
            panel_revoke_tasks.insert()
            .values(
                task_uuid=task.task_uuid,
                vpn_configuration_id=task.vpn_configuration_id,
                subscription_id=task.subscription_id,
                vpn_generation=task.vpn_generation,
                status=str(task.status),
                idempotency_key=task.idempotency_key,
                payload=task.payload,
                attempts=task.attempts,
                max_attempts=task.max_attempts,
                next_retry_at=task.next_retry_at,
                last_error=task.last_error,
                claimed_at=task.claimed_at,
                lease_expires_at=task.lease_expires_at,
                lease_token=task.lease_token,
                completed_at=task.completed_at,
            )
            .returning(panel_revoke_tasks)
        )
        row = result.mappings().first()
        if row is None:
            raise RuntimeError("Failed to persist panel revoke task")
        return panel_revoke_task_from_row(row)

    async def save_panel_revoke_task_entity(self, task: PanelRevokeTask) -> None:
        if task.id is None:
            raise ValueError("Cannot save panel revoke task without id")
        await self.connection.execute(
            update(panel_revoke_tasks)
            .where(panel_revoke_tasks.c.panel_revoke_task_id == task.id)
            .values(
                status=str(task.status),
                payload=task.payload,
                attempts=task.attempts,
                max_attempts=task.max_attempts,
                next_retry_at=task.next_retry_at,
                last_error=task.last_error,
                claimed_at=task.claimed_at,
                lease_expires_at=task.lease_expires_at,
                lease_token=task.lease_token,
                updated_at=task.updated_at or datetime.now(UTC),
                completed_at=task.completed_at,
            )
        )

    async def list_due_panel_revoke_task_ids(self, now: datetime, limit: int = 100) -> list[int]:
        result = await self.connection.execute(
            select(panel_revoke_tasks.c.panel_revoke_task_id)
            .where(
                panel_revoke_tasks.c.status == "pending",
                panel_revoke_tasks.c.next_retry_at <= now,
            )
            .order_by(panel_revoke_tasks.c.next_retry_at.asc())
            .limit(limit)
        )
        return [int(value) for value in result.scalars().all()]

    async def list_stale_panel_revoke_task_entities(
        self, now: datetime, limit: int = 100
    ) -> list[PanelRevokeTask]:
        query: Select[Any] = (
            select(panel_revoke_tasks)
            .where(
                panel_revoke_tasks.c.status == "in_progress",
                panel_revoke_tasks.c.lease_expires_at.is_not(None),
                panel_revoke_tasks.c.lease_expires_at <= now,
            )
            .order_by(panel_revoke_tasks.c.lease_expires_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        result = await self.connection.execute(query)
        return [panel_revoke_task_from_row(row) for row in result.mappings().all()]

    async def list_active_configuration_ids_due_for_revoke(self, now: datetime) -> list[int]:
        query: Select[Any] = (
            select(vpn_configurations.c.vpn_configuration_id)
            .join(subscriptions, subscriptions.c.subscription_id == vpn_configurations.c.subscription_id)
            .where(vpn_configurations.c.status == "active")
            .where(
                (subscriptions.c.status != "active")
                | ~subscriptions.c.subscription_id.in_(
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

    async def get_entity(
        self, vpn_configuration_id: int, *, for_update: bool = False
    ) -> VpnConfiguration | None:
        query: Select[Any] = (
            select(vpn_configurations)
            .where(vpn_configurations.c.vpn_configuration_id == vpn_configuration_id)
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        result = await self.connection.execute(query)
        row = result.mappings().first()
        return vpn_configuration_from_row(row) if row is not None else None

    async def get_active_entity_for_subscription(self, subscription_id: int) -> VpnConfiguration | None:
        row = await self.get_active_configuration_for_subscription(subscription_id)
        return vpn_configuration_from_row(row) if row is not None else None

    async def get_latest_entity_for_subscription(self, subscription_id: int) -> VpnConfiguration | None:
        row = await self.get_latest_configuration_for_subscription(subscription_id)
        return vpn_configuration_from_row(row) if row is not None else None

    async def add_entity(self, configuration: VpnConfiguration) -> VpnConfiguration:
        configuration.id = await self.create_configuration(
            subscription_id=configuration.subscription_id,
            server_endpoint_id=configuration.server_endpoint_id,
            client_uuid=configuration.client_uuid,
            display_name=configuration.display_name,
            status=str(configuration.status),
            desired_state=str(configuration.desired_state),
            generation=configuration.generation,
            remote_client_ref=configuration.remote_client_ref,
        )
        return configuration

    async def save_entity(self, configuration: VpnConfiguration) -> None:
        if configuration.id is None:
            raise ValueError("Cannot save VPN configuration without id")
        await self.connection.execute(
            update(vpn_configurations)
            .where(vpn_configurations.c.vpn_configuration_id == configuration.id)
            .values(
                status=str(configuration.status),
                desired_state=str(configuration.desired_state),
                generation=configuration.generation,
                remote_client_ref=configuration.remote_client_ref,
                revoked_at=configuration.revoked_at,
            )
        )
