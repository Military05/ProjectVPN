from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Select, and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from shop_bot.infrastructure.db.tables import (
    node_credentials,
    node_status,
    node_task_attempts,
    node_tasks,
    nodes,
)


class NodeRepository:
    def __init__(self, connection: AsyncConnection):
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
            nodes.insert()
            .values(
                node_key=node_key,
                display_name=display_name,
                api_base_url=api_base_url,
                is_enabled=is_enabled,
                selection_weight=selection_weight,
                status=status,
            )
            .returning(nodes)
        )
        row = result.mappings().first()
        if row is None:
            raise RuntimeError("Failed to create node")
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
        last_seen_at: datetime,
        last_error: str | None = None,
    ) -> None:
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
                updated_at=last_seen_at,
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
                    "updated_at": last_seen_at,
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
                updated_at=last_seen_at,
                last_error=last_error,
            )
        )

    async def mark_node_unreachable(self, node_id: int, *, error_message: str, seen_at: datetime) -> None:
        await self.connection.execute(
            update(nodes)
            .where(nodes.c.node_id == node_id)
            .values(status="offline", last_error=error_message, updated_at=seen_at)
        )
        await self.upsert_node_status(
            node_id=node_id,
            health_status="offline",
            agent_version=None,
            capabilities={},
            metrics={},
            status_payload={},
            inbounds=[],
            last_seen_at=seen_at,
            last_error=error_message,
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

    async def create_task(
        self,
        *,
        node_id: int,
        operation: str,
        idempotency_key: str,
        payload: dict[str, Any],
        vpn_configuration_id: int | None = None,
        subscription_id: int | None = None,
        max_attempts: int = 5,
        next_retry_at: datetime | None = None,
    ) -> Mapping[str, Any]:
        statement = (
            pg_insert(node_tasks)
            .values(
                task_uuid=uuid4(),
                node_id=node_id,
                vpn_configuration_id=vpn_configuration_id,
                subscription_id=subscription_id,
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
