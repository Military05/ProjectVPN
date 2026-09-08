from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import orjson


class JournalClaimKind(StrEnum):
    OWNER = "owner"
    REPLAY = "replay"
    CONFLICT = "conflict"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True, slots=True)
class JournalClaim:
    kind: JournalClaimKind
    owner_token: str | None = None
    response: dict[str, Any] | None = None
    reclaimed: bool = False


class NodeOperationJournal:
    def __init__(self, path: str, *, lease_seconds: int, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.lease_seconds = lease_seconds
        self.busy_timeout_ms = busy_timeout_ms

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def close(self) -> None:
        return None

    async def prune_completed(self, before: datetime) -> int:
        return await asyncio.to_thread(self._prune_completed_sync, before)

    async def retire(self, idempotency_key: str) -> str:
        return await asyncio.to_thread(self._retire_sync, idempotency_key)

    async def claim(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        operation: str,
        client_uuid: str,
        inbound_id: str,
        now: datetime | None = None,
    ) -> JournalClaim:
        at = now or datetime.now(UTC)
        return await asyncio.to_thread(
            self._claim_sync,
            idempotency_key,
            request_hash,
            operation,
            client_uuid,
            inbound_id,
            at,
        )

    async def complete(
        self,
        *,
        idempotency_key: str,
        owner_token: str,
        response: dict[str, Any],
        now: datetime | None = None,
    ) -> bool:
        at = now or datetime.now(UTC)
        return await asyncio.to_thread(
            self._complete_sync,
            idempotency_key,
            owner_token,
            response,
            at,
        )

    async def mark_retryable(
        self,
        *,
        idempotency_key: str,
        owner_token: str,
        error: str,
        now: datetime | None = None,
    ) -> bool:
        at = now or datetime.now(UTC)
        return await asyncio.to_thread(
            self._mark_retryable_sync,
            idempotency_key,
            owner_token,
            error,
            at,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return connection

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS node_operation_journal (
                    idempotency_key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    client_uuid TEXT NOT NULL,
                    inbound_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT NULL,
                    owner_token TEXT NULL,
                    lease_expires_at TEXT NULL,
                    last_error TEXT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (status IN ('in_progress', 'completed', 'retryable'))
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_node_operation_journal_status_lease "
                "ON node_operation_journal(status, lease_expires_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_node_operation_journal_status_updated "
                "ON node_operation_journal(status, updated_at)"
            )

    def _claim_sync(
        self,
        idempotency_key: str,
        request_hash: str,
        operation: str,
        client_uuid: str,
        inbound_id: str,
        now: datetime,
    ) -> JournalClaim:
        owner_token = uuid4().hex
        lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        now_text = _timestamp(now)
        lease_text = _timestamp(lease_expires_at)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM node_operation_journal WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO node_operation_journal (
                        idempotency_key, request_hash, operation, client_uuid, inbound_id,
                        status, response_json, owner_token, lease_expires_at, last_error,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'in_progress', NULL, ?, ?, NULL, ?, ?)
                    """,
                    (
                        idempotency_key,
                        request_hash,
                        operation,
                        client_uuid,
                        inbound_id,
                        owner_token,
                        lease_text,
                        now_text,
                        now_text,
                    ),
                )
                connection.execute("COMMIT")
                return JournalClaim(JournalClaimKind.OWNER, owner_token=owner_token)

            if (
                row["request_hash"] != request_hash
                or row["operation"] != operation
                or row["client_uuid"] != client_uuid
                or row["inbound_id"] != inbound_id
            ):
                connection.execute("COMMIT")
                return JournalClaim(JournalClaimKind.CONFLICT)

            if row["status"] == "completed":
                response = orjson.loads(row["response_json"]) if row["response_json"] else {}
                connection.execute("COMMIT")
                return JournalClaim(JournalClaimKind.REPLAY, response=response)

            live_lease = False
            if row["status"] == "in_progress" and row["lease_expires_at"]:
                live_lease = _parse_timestamp(row["lease_expires_at"]) > now
            if live_lease:
                connection.execute("COMMIT")
                return JournalClaim(JournalClaimKind.IN_PROGRESS)

            connection.execute(
                """
                UPDATE node_operation_journal
                SET status = 'in_progress', owner_token = ?, lease_expires_at = ?,
                    last_error = NULL, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (owner_token, lease_text, now_text, idempotency_key),
            )
            connection.execute("COMMIT")
            return JournalClaim(
                JournalClaimKind.OWNER,
                owner_token=owner_token,
                reclaimed=True,
            )
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def _complete_sync(
        self,
        idempotency_key: str,
        owner_token: str,
        response: dict[str, Any],
        now: datetime,
    ) -> bool:
        response_json = orjson.dumps(response, option=orjson.OPT_SORT_KEYS).decode("utf-8")
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE node_operation_journal
                SET status = 'completed', response_json = ?, owner_token = NULL,
                    lease_expires_at = NULL, last_error = NULL, updated_at = ?
                WHERE idempotency_key = ? AND status = 'in_progress' AND owner_token = ?
                """,
                (response_json, _timestamp(now), idempotency_key, owner_token),
            )
            return result.rowcount == 1

    def _mark_retryable_sync(
        self,
        idempotency_key: str,
        owner_token: str,
        error: str,
        now: datetime,
    ) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE node_operation_journal
                SET status = 'retryable', owner_token = NULL, lease_expires_at = NULL,
                    last_error = ?, updated_at = ?
                WHERE idempotency_key = ? AND status = 'in_progress' AND owner_token = ?
                """,
                (error, _timestamp(now), idempotency_key, owner_token),
            )
            return result.rowcount == 1

    def _prune_completed_sync(self, before: datetime) -> int:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM node_operation_journal WHERE status = 'completed' AND updated_at < ?",
                (_timestamp(before),),
            )
            return int(result.rowcount)

    def _retire_sync(self, idempotency_key: str) -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM node_operation_journal WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if row is None:
                return "already_retired"
            if row["status"] == "in_progress":
                return "operation_in_progress"
            connection.execute("DELETE FROM node_operation_journal WHERE idempotency_key = ?", (idempotency_key,))
            return "retired"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
