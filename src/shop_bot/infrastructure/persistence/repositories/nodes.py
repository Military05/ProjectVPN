from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from shop_bot.core.exceptions import ConflictError

from shop_bot.domain.entities.node import Node, NodeTask
from shop_bot.infrastructure.persistence.sqlalchemy.mappers import node_from_row, node_task_from_row
from shop_bot.infrastructure.persistence.sqlalchemy.tables import (
    node_credentials,
    node_status,
    node_task_attempts,
    node_tasks,
    nodes,
)


@dataclass(frozen=True, slots=True)
class NodeProbeClaim:
    node_id: int
    lease_token: UUID
    node: Mapping[str, Any]


class NodeRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def create_node(
        self,
        *,
        node_key: str,
        display_name: str,
        api_base_url: str,
        is_enabled: bool = True,
        selection_weight: int = 100,
        status: str = "unknown",
    ) -> Mapping[str, Any]:
        result = await self.connection.execute(
            pg_insert(nodes)
            .values(
                node_key=node_key,
                display_name=display_name,
                api_base_url=api_base_url,
                is_enabled=is_enabled,
                selection_weight=selection_weight,
                status=status,
            )
            .on_conflict_do_nothing()
            .returning(nodes)
        )
        row = result.mappings().first()
        if row is None:
            raise ConflictError("Node with the same key already exists")
        return row

    async def list_nodes(self, limit: int = 100, offset: int = 0) -> list[Mapping[str, Any]]:
        query: Select[Any] = (
            select(nodes, node_status.c.health_status, node_status.c.agent_version, node_status.c.last_seen_at)
            .outerjoin(node_status, node_status.c.node_id == nodes.c.node_id)
            .order_by(nodes.c.node_id.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.connection.execute(query)
        return list(result.mappings().all())

    async def get_node(self, node_id: int, *, for_update: bool = False) -> Mapping[str, Any] | None:
        query: Select[Any] = select(nodes).where(nodes.c.node_id == node_id).limit(1)
        if for_update:
            query = query.with_for_update()
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_node_by_key(self, node_key: str) -> Mapping[str, Any] | None:
        result = await self.connection.execute(select(nodes).where(nodes.c.node_key == node_key).limit(1))
        return result.mappings().first()

    async def create_credential(
        self,
        *,
        node_id: int,
        key_id: str,
        shared_secret: str,
        is_active: bool = True,
        expires_at: datetime | None = None,
    ) -> Mapping[str, Any]:
        if is_active:
            await self.connection.execute(
                update(node_credentials)
                .where(node_credentials.c.node_id == node_id, node_credentials.c.is_active.is_(True))
                .values(is_active=False)
            )
        result = await self.connection.execute(
            node_credentials.insert()
            .values(
                node_id=node_id,
                key_id=key_id,
                shared_secret=shared_secret,
                is_active=is_active,
                expires_at=expires_at,
            )
            .returning(node_credentials)
        )
        row = result.mappings().first()
        if row is None:
            raise RuntimeError("Failed to create node credential")
        return row

    async def get_active_credential(self, node_id: int) -> Mapping[str, Any] | None:
        result = await self.connection.execute(
            select(node_credentials)
            .where(node_credentials.c.node_id == node_id, node_credentials.c.is_active.is_(True))
            .order_by(node_credentials.c.created_at.desc())
            .limit(1)
        )
        return result.mappings().first()

    async def upsert_node_status(
        self,
        *,
        node_id: int,
        health_status: str,
        agent_version: str | None,
        capabilities: dict[str, Any],
        metrics: dict[str, Any],
        status_payload: dict[str, Any],
        inbounds: list[dict[str, Any]],
        last_seen_at: datetime | None = None,
        last_error: str | None = None,
        last_checked_at: datetime | None = None,
        last_failed_at: datetime | None = None,
    ) -> None:
        checked_at = last_checked_at or last_seen_at or datetime.now(UTC)
        statement = (
            pg_insert(node_status)
            .values(
                node_id=node_id,
                health_status=health_status,
                agent_version=agent_version,
                capabilities=capabilities,
                metrics=metrics,
                status_payload=status_payload,
                inbounds=inbounds,
                last_seen_at=last_seen_at,
                last_checked_at=checked_at,
                last_failed_at=last_failed_at,
                updated_at=checked_at,
            )
            .on_conflict_do_update(
                index_elements=[node_status.c.node_id],
                set_={
                    "health_status": health_status,
                    "agent_version": agent_version,
                    "capabilities": capabilities,
                    "metrics": metrics,
                    "status_payload": status_payload,
                    "inbounds": inbounds,
                    "last_seen_at": last_seen_at,
                    "last_checked_at": checked_at,
                    "last_failed_at": last_failed_at,
                    "active_clients": metrics.get("active_clients"),
                    "max_clients": metrics.get("max_clients"),
                    "updated_at": checked_at,
                },
            )
        )
        await self.connection.execute(statement)
        await self.connection.execute(
            update(nodes)
            .where(nodes.c.node_id == node_id)
            .values(
                status=health_status,
                last_seen_at=last_seen_at,
                last_checked_at=checked_at,
                last_failed_at=last_failed_at,
                updated_at=checked_at,
                last_error=last_error,
            )
        )

    async def mark_node_unreachable(self, node_id: int, *, error_message: str, seen_at: datetime) -> None:
        await self.connection.execute(
            update(nodes)
            .where(nodes.c.node_id == node_id)
            .values(status="offline", last_error=error_message, updated_at=seen_at)
        )
        await self.connection.execute(
            pg_insert(node_status).values(node_id=node_id, health_status="offline", last_checked_at=seen_at, last_failed_at=seen_at, updated_at=seen_at)
            .on_conflict_do_update(index_elements=[node_status.c.node_id], set_={"health_status": "offline", "last_checked_at": seen_at, "last_failed_at": seen_at, "updated_at": seen_at})
        )

    async def touch_node_success(self, node_id: int, *, seen_at: datetime) -> None:
        await self.connection.execute(
            update(nodes)
            .where(nodes.c.node_id == node_id)
            .values(status="online", last_seen_at=seen_at, updated_at=seen_at, last_error=None)
        )
        await self.connection.execute(
            update(node_status)
            .where(node_status.c.node_id == node_id)
            .values(health_status="online", last_seen_at=seen_at, updated_at=seen_at)
        )

    async def claim_due_probe_targets(self, now: datetime, due_before: datetime, limit: int = 100, lease_seconds: int = 30) -> list[NodeProbeClaim]:
        result = await self.connection.execute(
            select(node_status, nodes)
            .join(nodes, nodes.c.node_id == node_status.c.node_id)
            .where(nodes.c.is_enabled.is_(True))
            .where((node_status.c.last_checked_at.is_(None)) | (node_status.c.last_checked_at <= due_before))
            .where((node_status.c.probe_lease_expires_at.is_(None)) | (node_status.c.probe_lease_expires_at <= now))
            .order_by(node_status.c.last_checked_at.asc().nullsfirst(), nodes.c.node_id.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        claims: list[NodeProbeClaim] = []
        for row in result.mappings().all():
            node_id = int(row["node_id"])
            token = uuid4()
            await self.connection.execute(
                update(node_status).where(node_status.c.node_id == node_id).values(
                    probe_lease_token=token,
                    probe_lease_expires_at=now + timedelta(seconds=lease_seconds),
                )
            )
            claims.append(NodeProbeClaim(node_id=node_id, lease_token=token, node=row))
        return claims

    async def finalize_probe(self, *, node_id: int, lease_token: UUID, health_status: str, completed_at: datetime, last_seen_at: datetime | None, last_error: str | None, capabilities: dict[str, Any], metrics: dict[str, Any], status_payload: dict[str, Any], inbounds: list[dict[str, Any]], agent_version: str | None) -> bool:
        result = await self.connection.execute(
            update(node_status).where(node_status.c.node_id == node_id, node_status.c.probe_lease_token == lease_token).values(
                health_status=health_status, agent_version=agent_version, capabilities=capabilities,
                metrics=metrics, status_payload=status_payload, inbounds=inbounds,
                last_seen_at=last_seen_at, last_checked_at=completed_at,
                last_failed_at=completed_at if last_error else None,
                active_clients=metrics.get("active_clients"), max_clients=metrics.get("max_clients"),
                probe_lease_token=None, probe_lease_expires_at=None, updated_at=completed_at,
            )
        )
        if result.rowcount != 1:
            return False
        await self.connection.execute(
            update(nodes).where(nodes.c.node_id == node_id).values(
                status=health_status, last_checked_at=completed_at,
                last_failed_at=completed_at if last_error else None,
                last_seen_at=last_seen_at, last_error=last_error, updated_at=completed_at,
            )
        )
        return True

    async def create_task(
        self,
        *,
        node_id: int,
        operation: str,
        idempotency_key: str,
        payload: dict[str, Any],
        vpn_configuration_id: int | None = None,
        subscription_id: int | None = None,
        vpn_generation: int | None = None,
        max_attempts: int = 5,
        next_retry_at: datetime | None = None,
        task_uuid: UUID | None = None,
    ) -> Mapping[str, Any]:
        statement = (
            pg_insert(node_tasks)
            .values(
                task_uuid=task_uuid or uuid4(),
                node_id=node_id,
                vpn_configuration_id=vpn_configuration_id,
                subscription_id=subscription_id,
                vpn_generation=vpn_generation,
                operation=operation,
                status="pending",
                idempotency_key=idempotency_key,
                payload=payload,
                max_attempts=max_attempts,
                next_retry_at=next_retry_at or datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=[node_tasks.c.idempotency_key])
            .returning(node_tasks)
        )
        result = await self.connection.execute(statement)
        row = result.mappings().first()
        if row is not None:
            return row
        existing = await self.get_task_by_idempotency(idempotency_key)
        if existing is None:
            raise RuntimeError("Failed to create node task")
        return existing

    async def get_task_by_idempotency(self, idempotency_key: str) -> Mapping[str, Any] | None:
        result = await self.connection.execute(
            select(node_tasks).where(node_tasks.c.idempotency_key == idempotency_key).limit(1)
        )
        return result.mappings().first()

    async def get_task(self, node_task_id: int, *, for_update: bool = False) -> Mapping[str, Any] | None:
        query: Select[Any] = select(node_tasks).where(node_tasks.c.node_task_id == node_task_id).limit(1)
        if for_update:
            query = query.with_for_update()
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def get_vpn_task_for_generation(
        self,
        vpn_configuration_id: int,
        operation: str,
        vpn_generation: int,
    ) -> NodeTask | None:
        result = await self.connection.execute(
            select(node_tasks)
            .where(
                node_tasks.c.vpn_configuration_id == vpn_configuration_id,
                node_tasks.c.operation == operation,
                node_tasks.c.vpn_generation == vpn_generation,
                node_tasks.c.status.in_(("pending", "in_progress", "succeeded", "failed", "cancelled")),
            )
            .order_by(node_tasks.c.node_task_id.desc())
            .limit(1)
        )
        row = result.mappings().first()
        return node_task_from_row(row) if row is not None else None

    async def list_tasks(self, limit: int = 100, offset: int = 0) -> list[Mapping[str, Any]]:
        result = await self.connection.execute(
            select(node_tasks)
            .order_by(node_tasks.c.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.mappings().all())

    async def list_due_tasks(self, now: datetime, limit: int = 100) -> list[int]:
        result = await self.connection.execute(
            select(node_tasks.c.node_task_id)
            .where(
                node_tasks.c.status == "pending",
                node_tasks.c.next_retry_at <= now,
            )
            .order_by(node_tasks.c.next_retry_at.asc(), node_tasks.c.node_task_id.asc())
            .limit(limit)
        )
        return [int(value) for value in result.scalars().all()]

    async def list_stale_task_entities(self, now: datetime, limit: int = 100) -> list[NodeTask]:
        query: Select[Any] = (
            select(node_tasks)
            .where(
                node_tasks.c.status == "in_progress",
                node_tasks.c.lease_expires_at.is_not(None),
                node_tasks.c.lease_expires_at <= now,
            )
            .order_by(node_tasks.c.lease_expires_at.asc(), node_tasks.c.node_task_id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.connection.execute(query)
        return [node_task_from_row(row) for row in result.mappings().all()]

    async def mark_task_in_progress(self, node_task_id: int, *, started_at: datetime) -> Mapping[str, Any] | None:
        result = await self.connection.execute(
            update(node_tasks)
            .where(node_tasks.c.node_task_id == node_task_id)
            .values(
                status="in_progress",
                attempts=node_tasks.c.attempts + 1,
                updated_at=started_at,
                last_error=None,
            )
            .returning(node_tasks)
        )
        return result.mappings().first()

    async def mark_task_succeeded(
        self,
        node_task_id: int,
        *,
        response_payload: dict[str, Any],
        remote_client_ref: str | None,
        completed_at: datetime,
    ) -> None:
        await self.connection.execute(
            update(node_tasks)
            .where(node_tasks.c.node_task_id == node_task_id)
            .values(
                status="succeeded",
                response_payload=response_payload,
                remote_client_ref=remote_client_ref,
                completed_at=completed_at,
                updated_at=completed_at,
                last_error=None,
            )
        )

    async def mark_task_retry(
        self,
        node_task_id: int,
        *,
        next_retry_at: datetime,
        last_error: str,
        response_payload: dict[str, Any] | None,
    ) -> None:
        values: dict[str, Any] = {
            "status": "pending",
            "next_retry_at": next_retry_at,
            "last_error": last_error,
            "updated_at": datetime.now(UTC),
        }
        if response_payload is not None:
            values["response_payload"] = response_payload
        await self.connection.execute(update(node_tasks).where(node_tasks.c.node_task_id == node_task_id).values(**values))

    async def mark_task_failed(
        self,
        node_task_id: int,
        *,
        last_error: str,
        response_payload: dict[str, Any] | None,
        completed_at: datetime,
    ) -> None:
        values: dict[str, Any] = {
            "status": "failed",
            "last_error": last_error,
            "completed_at": completed_at,
            "updated_at": completed_at,
        }
        if response_payload is not None:
            values["response_payload"] = response_payload
        await self.connection.execute(update(node_tasks).where(node_tasks.c.node_task_id == node_task_id).values(**values))

    async def create_task_attempt(
        self,
        *,
        node_task_id: int,
        attempt_no: int,
        request_payload: dict[str, Any],
        started_at: datetime,
    ) -> Mapping[str, Any]:
        result = await self.connection.execute(
            node_task_attempts.insert()
            .values(
                node_task_id=node_task_id,
                attempt_no=attempt_no,
                status="started",
                request_payload=request_payload,
                started_at=started_at,
            )
            .returning(node_task_attempts)
        )
        row = result.mappings().first()
        if row is None:
            raise RuntimeError("Failed to create node task attempt")
        return row

    async def finish_task_attempt(
        self,
        node_task_attempt_id: int,
        *,
        status: str,
        finished_at: datetime,
        response_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "finished_at": finished_at,
            "error_message": error_message,
        }
        if response_payload is not None:
            values["response_payload"] = response_payload
        await self.connection.execute(
            update(node_task_attempts)
            .where(node_task_attempts.c.node_task_attempt_id == node_task_attempt_id)
            .values(**values)
        )

    async def fail_started_task_attempt(
        self,
        *,
        node_task_id: int,
        attempt_no: int,
        finished_at: datetime,
        error_message: str,
    ) -> bool:
        result = await self.connection.execute(
            update(node_task_attempts)
            .where(
                node_task_attempts.c.node_task_id == node_task_id,
                node_task_attempts.c.attempt_no == attempt_no,
                node_task_attempts.c.status == "started",
                node_task_attempts.c.finished_at.is_(None),
            )
            .values(
                status="failed",
                finished_at=finished_at,
                error_message=error_message,
            )
            .returning(node_task_attempts.c.node_task_attempt_id)
        )
        return result.scalar_one_or_none() is not None

    async def get_active_node_for_endpoint(self, endpoint_node_id: int | None) -> Mapping[str, Any] | None:
        if endpoint_node_id is None:
            return None
        query: Select[Any] = (
            select(nodes, node_status.c.health_status)
            .outerjoin(node_status, node_status.c.node_id == nodes.c.node_id)
            .where(nodes.c.node_id == endpoint_node_id, nodes.c.is_enabled.is_(True))
            .limit(1)
        )
        result = await self.connection.execute(query)
        return result.mappings().first()

    async def list_enabled_nodes(self) -> list[Mapping[str, Any]]:
        query: Select[Any] = (
            select(nodes, node_status.c.health_status, node_status.c.last_seen_at)
            .outerjoin(node_status, node_status.c.node_id == nodes.c.node_id)
            .where(nodes.c.is_enabled.is_(True))
            .order_by(nodes.c.selection_weight.desc(), nodes.c.node_id.asc())
        )
        result = await self.connection.execute(query)
        return list(result.mappings().all())

    async def get_node_entity(self, node_id: int, *, for_update: bool = False) -> Node | None:
        row = await self.get_node(node_id, for_update=for_update)
        return node_from_row(row) if row is not None else None

    async def save_node_entity(self, node: Node) -> None:
        if node.id is None:
            raise ValueError("Cannot save node without id")
        await self.connection.execute(
            update(nodes)
            .where(nodes.c.node_id == node.id)
            .values(
                display_name=node.display_name,
                api_base_url=node.api_base_url,
                status=str(node.status),
                is_enabled=node.is_enabled,
                selection_weight=node.selection_weight,
                last_seen_at=node.last_seen_at,
                last_error=node.last_error,
                updated_at=datetime.now(UTC),
            )
        )

    async def get_task_entity(self, node_task_id: int, *, for_update: bool = False) -> NodeTask | None:
        row = await self.get_task(node_task_id, for_update=for_update)
        return node_task_from_row(row) if row is not None else None

    async def add_task_entity(self, task: NodeTask) -> NodeTask:
        row = await self.create_task(
            node_id=task.node_id,
            operation=str(task.operation),
            idempotency_key=task.idempotency_key,
            payload=task.payload,
            vpn_configuration_id=task.vpn_configuration_id,
            subscription_id=task.subscription_id,
            vpn_generation=task.vpn_generation,
            max_attempts=task.max_attempts,
            next_retry_at=task.next_retry_at,
            task_uuid=task.task_uuid,
        )
        return node_task_from_row(row)

    async def save_task_entity(self, task: NodeTask) -> None:
        if task.id is None:
            raise ValueError("Cannot save node task without id")
        await self.connection.execute(
            update(node_tasks)
            .where(node_tasks.c.node_task_id == task.id)
            .values(
                status=str(task.status),
                vpn_generation=task.vpn_generation,
                response_payload=task.response_payload,
                remote_client_ref=task.remote_client_ref,
                attempts=task.attempts,
                next_retry_at=task.next_retry_at,
                last_error=task.last_error,
                claimed_at=task.claimed_at,
                lease_expires_at=task.lease_expires_at,
                lease_token=task.lease_token,
                updated_at=task.updated_at or datetime.now(UTC),
                completed_at=task.completed_at,
                journal_retired_at=task.journal_retired_at,
            )
        )
