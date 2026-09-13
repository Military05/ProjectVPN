from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, exists, literal, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import ColumnElement, Select

from shop_bot.domain.reconciliation import (
    ReconciliationAnomaly,
    ReconciliationAnomalyKind,
)
from shop_bot.infrastructure.persistence.sqlalchemy.tables import (
    maintenance_leases,
    node_tasks,
    panel_provision_tasks,
    panel_revoke_tasks,
    server_endpoints,
    subscription_periods,
    subscriptions,
    vpn_configurations,
)


_BLOCKING_VPN_STATUSES = (
    "provisioning",
    "active",
    "failed",
    "revoking",
    "revoke_failed",
)
_REMOTE_LIVE_VPN_STATUSES = (
    "provisioning",
    "active",
    "failed",
    "revoke_failed",
)
_TERMINAL_TASK_STATUSES = ("succeeded", "failed", "cancelled")


class MaintenanceRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def acquire_lease(
        self,
        *,
        lease_name: str,
        owner_token: UUID,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        result = await self.connection.execute(
            pg_insert(maintenance_leases)
            .values(
                lease_name=lease_name,
                owner_token=owner_token,
                lease_expires_at=lease_expires_at,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[maintenance_leases.c.lease_name],
                set_={
                    "owner_token": owner_token,
                    "lease_expires_at": lease_expires_at,
                    "updated_at": now,
                },
                where=maintenance_leases.c.lease_expires_at <= now,
            )
            .returning(maintenance_leases.c.owner_token)
        )
        return result.scalar_one_or_none() == owner_token

    async def renew_lease(
        self,
        *,
        lease_name: str,
        owner_token: UUID,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        result = await self.connection.execute(
            update(maintenance_leases)
            .where(
                maintenance_leases.c.lease_name == lease_name,
                maintenance_leases.c.owner_token == owner_token,
            )
            .values(
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
            .returning(maintenance_leases.c.owner_token)
        )
        return result.scalar_one_or_none() == owner_token

    async def release_lease(self, *, lease_name: str, owner_token: UUID) -> bool:
        result = await self.connection.execute(
            delete(maintenance_leases)
            .where(
                maintenance_leases.c.lease_name == lease_name,
                maintenance_leases.c.owner_token == owner_token,
            )
            .returning(maintenance_leases.c.owner_token)
        )
        return result.scalar_one_or_none() == owner_token

    async def list_reconciliation_anomalies(
        self,
        *,
        now: datetime,
        after_kind_order: int,
        after_entity_id: int,
        limit: int,
    ) -> list[ReconciliationAnomaly]:
        current_period = exists(
            select(1)
            .select_from(subscription_periods)
            .where(
                subscription_periods.c.subscription_id == subscriptions.c.subscription_id,
                subscription_periods.c.is_paid.is_(True),
                subscription_periods.c.starts_at <= now,
                subscription_periods.c.expires_at > now,
            )
        )

        expire_subscriptions = self._anomaly_select(
            1,
            ReconciliationAnomalyKind.EXPIRE_SUBSCRIPTION,
            subscriptions.c.subscription_id,
        ).where(
            subscriptions.c.status == "active",
            ~current_period,
        )

        revoke_vpn = (
            self._anomaly_select(
                2,
                ReconciliationAnomalyKind.REVOKE_VPN,
                vpn_configurations.c.vpn_configuration_id,
            )
            .select_from(
                vpn_configurations.join(
                    subscriptions,
                    subscriptions.c.subscription_id == vpn_configurations.c.subscription_id,
                )
            )
            .where(
                vpn_configurations.c.status.in_(_REMOTE_LIVE_VPN_STATUSES),
                or_(subscriptions.c.status != "active", ~current_period),
            )
        )

        cleanup_vpn = (
            self._anomaly_select(
                3,
                ReconciliationAnomalyKind.CLEANUP_VPN,
                vpn_configurations.c.vpn_configuration_id,
            )
            .select_from(
                vpn_configurations.join(
                    subscriptions,
                    subscriptions.c.subscription_id == vpn_configurations.c.subscription_id,
                )
            )
            .where(
                vpn_configurations.c.status == "failed",
                subscriptions.c.status == "active",
                current_period,
            )
        )

        live_or_inflight_vpn = exists(
            select(1)
            .select_from(vpn_configurations)
            .where(
                vpn_configurations.c.subscription_id == subscriptions.c.subscription_id,
                vpn_configurations.c.status.in_(_BLOCKING_VPN_STATUSES),
            )
        )
        provision_subscriptions = self._anomaly_select(
            4,
            ReconciliationAnomalyKind.PROVISION_SUBSCRIPTION,
            subscriptions.c.subscription_id,
        ).where(
            subscriptions.c.status == "active",
            current_period,
            ~live_or_inflight_vpn,
        )

        node_provision_task = exists(
            select(1)
            .select_from(node_tasks)
            .where(
                node_tasks.c.vpn_configuration_id
                == vpn_configurations.c.vpn_configuration_id,
                node_tasks.c.operation == "provision_client",
                node_tasks.c.vpn_generation == vpn_configurations.c.generation,
            )
        )
        terminal_node_provision_task = exists(
            select(1)
            .select_from(node_tasks)
            .where(
                node_tasks.c.vpn_configuration_id
                == vpn_configurations.c.vpn_configuration_id,
                node_tasks.c.operation == "provision_client",
                node_tasks.c.vpn_generation == vpn_configurations.c.generation,
                node_tasks.c.status.in_(_TERMINAL_TASK_STATUSES),
            )
        )
        panel_provision_task = exists(
            select(1)
            .select_from(panel_provision_tasks)
            .where(
                panel_provision_tasks.c.vpn_configuration_id
                == vpn_configurations.c.vpn_configuration_id,
                panel_provision_tasks.c.vpn_generation == vpn_configurations.c.generation,
            )
        )
        terminal_panel_provision_task = exists(
            select(1)
            .select_from(panel_provision_tasks)
            .where(
                panel_provision_tasks.c.vpn_configuration_id
                == vpn_configurations.c.vpn_configuration_id,
                panel_provision_tasks.c.vpn_generation == vpn_configurations.c.generation,
                panel_provision_tasks.c.status.in_(_TERMINAL_TASK_STATUSES),
            )
        )
        provision_needs_repair = or_(
            and_(
                server_endpoints.c.node_id.is_not(None),
                or_(~node_provision_task, terminal_node_provision_task),
            ),
            and_(
                server_endpoints.c.node_id.is_(None),
                or_(~panel_provision_task, terminal_panel_provision_task),
            ),
        )
        repair_provision = (
            self._anomaly_select(
                5,
                ReconciliationAnomalyKind.REPAIR_PROVISION,
                vpn_configurations.c.subscription_id,
            )
            .select_from(
                vpn_configurations.join(
                    subscriptions,
                    subscriptions.c.subscription_id == vpn_configurations.c.subscription_id,
                ).join(
                    server_endpoints,
                    server_endpoints.c.server_endpoint_id
                    == vpn_configurations.c.server_endpoint_id,
                )
            )
            .where(
                vpn_configurations.c.status == "provisioning",
                vpn_configurations.c.desired_state == "active",
                subscriptions.c.status == "active",
                current_period,
                provision_needs_repair,
            )
        )

        node_revoke_task = exists(
            select(1)
            .select_from(node_tasks)
            .where(
                node_tasks.c.vpn_configuration_id
                == vpn_configurations.c.vpn_configuration_id,
                node_tasks.c.operation == "revoke_client",
                node_tasks.c.vpn_generation == vpn_configurations.c.generation,
            )
        )
        succeeded_node_revoke_task = exists(
            select(1)
            .select_from(node_tasks)
            .where(
                node_tasks.c.vpn_configuration_id
                == vpn_configurations.c.vpn_configuration_id,
                node_tasks.c.operation == "revoke_client",
                node_tasks.c.vpn_generation == vpn_configurations.c.generation,
                node_tasks.c.status == "succeeded",
            )
        )
        panel_revoke_task = exists(
            select(1)
            .select_from(panel_revoke_tasks)
            .where(
                panel_revoke_tasks.c.vpn_configuration_id
                == vpn_configurations.c.vpn_configuration_id,
                panel_revoke_tasks.c.vpn_generation == vpn_configurations.c.generation,
            )
        )
        succeeded_panel_revoke_task = exists(
            select(1)
            .select_from(panel_revoke_tasks)
            .where(
                panel_revoke_tasks.c.vpn_configuration_id
                == vpn_configurations.c.vpn_configuration_id,
                panel_revoke_tasks.c.vpn_generation == vpn_configurations.c.generation,
                panel_revoke_tasks.c.status == "succeeded",
            )
        )
        revoke_needs_repair = or_(
            and_(
                server_endpoints.c.node_id.is_not(None),
                or_(~node_revoke_task, succeeded_node_revoke_task),
            ),
            and_(
                server_endpoints.c.node_id.is_(None),
                or_(~panel_revoke_task, succeeded_panel_revoke_task),
            ),
        )
        repair_revoke = (
            self._anomaly_select(
                6,
                ReconciliationAnomalyKind.REPAIR_REVOKE,
                vpn_configurations.c.vpn_configuration_id,
            )
            .select_from(
                vpn_configurations.join(
                    server_endpoints,
                    server_endpoints.c.server_endpoint_id
                    == vpn_configurations.c.server_endpoint_id,
                )
            )
            .where(
                vpn_configurations.c.status == "revoking",
                vpn_configurations.c.desired_state == "revoked",
                revoke_needs_repair,
            )
        )

        anomalies = union_all(
            expire_subscriptions,
            revoke_vpn,
            cleanup_vpn,
            provision_subscriptions,
            repair_provision,
            repair_revoke,
        ).subquery("reconciliation_anomalies")
        query = (
            select(
                anomalies.c.kind_order,
                anomalies.c.kind,
                anomalies.c.entity_id,
            )
            .where(
                or_(
                    anomalies.c.kind_order > after_kind_order,
                    and_(
                        anomalies.c.kind_order == after_kind_order,
                        anomalies.c.entity_id > after_entity_id,
                    ),
                )
            )
            .order_by(anomalies.c.kind_order.asc(), anomalies.c.entity_id.asc())
            .limit(limit)
        )
        result = await self.connection.execute(query)
        return [
            ReconciliationAnomaly(
                kind_order=int(row["kind_order"]),
                kind=ReconciliationAnomalyKind(str(row["kind"])),
                entity_id=int(row["entity_id"]),
            )
            for row in result.mappings().all()
        ]

    @staticmethod
    def _anomaly_select(
        kind_order: int,
        kind: ReconciliationAnomalyKind,
        entity_id: ColumnElement[Any],
    ) -> Select[Any]:
        return select(
            literal(kind_order).label("kind_order"),
            literal(kind.value).label("kind"),
            entity_id.label("entity_id"),
        )
