from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import sqlite3
from types import SimpleNamespace
from typing import Any

import orjson
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from shop_bot.apps.node_agent.routes import (
    JournalRetireRequest,
    _execute_operation,
    retire_journal,
)
from shop_bot.infrastructure.nodes.auth import VerifiedNodeRequest
from shop_bot.infrastructure.nodes.idempotency import JournalClaimKind, NodeOperationJournal


NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_node_agent_replays_same_idempotency_key_after_restart_without_second_owner(tmp_path) -> None:
    path = tmp_path / "journal.sqlite3"
    first = NodeOperationJournal(str(path), lease_seconds=30)
    await first.initialize()
    claim = await first.claim(
        idempotency_key="key-1",
        request_hash="hash-1",
        operation="provision",
        client_uuid="client-1",
        inbound_id="1",
        now=NOW,
    )
    assert claim.kind is JournalClaimKind.OWNER
    assert claim.owner_token is not None
    response = {"status": "provisioned", "client_uuid": "client-1", "remote_client_ref": "remote-1"}
    assert await first.complete(
        idempotency_key="key-1",
        owner_token=claim.owner_token,
        response=response,
        now=NOW,
    )
    await first.close()

    restarted = NodeOperationJournal(str(path), lease_seconds=30)
    await restarted.initialize()
    replay = await restarted.claim(
        idempotency_key="key-1",
        request_hash="hash-1",
        operation="provision",
        client_uuid="client-1",
        inbound_id="1",
        now=NOW + timedelta(seconds=1),
    )
    assert replay.kind is JournalClaimKind.REPLAY
    assert replay.response == response


@pytest.mark.asyncio
async def test_same_key_different_request_conflicts_and_concurrent_same_key_has_one_owner(tmp_path) -> None:
    journal = NodeOperationJournal(str(tmp_path / "journal.sqlite3"), lease_seconds=30)
    await journal.initialize()

    async def claim_same() -> Any:
        return await journal.claim(
            idempotency_key="same-key",
            request_hash="same-hash",
            operation="revoke",
            client_uuid="client-1",
            inbound_id="1",
            now=NOW,
        )

    claims = await asyncio.gather(*(claim_same() for _ in range(8)))
    assert sum(c.kind is JournalClaimKind.OWNER for c in claims) == 1
    assert sum(c.kind is JournalClaimKind.IN_PROGRESS for c in claims) == 7

    conflict = await journal.claim(
        idempotency_key="same-key",
        request_hash="different-hash",
        operation="revoke",
        client_uuid="client-1",
        inbound_id="1",
        now=NOW,
    )
    assert conflict.kind is JournalClaimKind.CONFLICT


@pytest.mark.asyncio
async def test_stale_journal_owner_cannot_finalize_after_lease_reclaim(tmp_path) -> None:
    journal = NodeOperationJournal(str(tmp_path / "journal.sqlite3"), lease_seconds=10)
    await journal.initialize()
    old = await journal.claim(
        idempotency_key="key",
        request_hash="hash",
        operation="provision",
        client_uuid="client",
        inbound_id="1",
        now=NOW,
    )
    assert old.owner_token
    new = await journal.claim(
        idempotency_key="key",
        request_hash="hash",
        operation="provision",
        client_uuid="client",
        inbound_id="1",
        now=NOW + timedelta(seconds=11),
    )
    assert new.kind is JournalClaimKind.OWNER
    assert new.reclaimed is True
    assert new.owner_token and new.owner_token != old.owner_token

    assert not await journal.complete(
        idempotency_key="key",
        owner_token=old.owner_token,
        response={"status": "provisioned", "client_uuid": "client"},
        now=NOW + timedelta(seconds=12),
    )
    assert await journal.complete(
        idempotency_key="key",
        owner_token=new.owner_token,
        response={"status": "provisioned", "client_uuid": "client"},
        now=NOW + timedelta(seconds=12),
    )


class RuntimeSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def provision_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("provision", str(payload["client_uuid"])))
        return {
            "status": "provisioned",
            "client_uuid": str(payload["client_uuid"]),
            "remote_client_ref": "remote-1",
        }

    async def revoke_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("revoke", str(payload["client_uuid"])))
        return {"status": "not_found_treated_as_success", "client_uuid": str(payload["client_uuid"])}


def make_request(*, body: bytes, path: str, journal: NodeOperationJournal, runtime: RuntimeSpy) -> Request:
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    app = SimpleNamespace(
        state=SimpleNamespace(
            operation_journal=journal,
            runtime=runtime,
            settings=SimpleNamespace(node_agent_runtime_mode="stub"),
        )
    )
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 123),
            "app": app,
        },
        receive,
    )


def verified(key: str) -> VerifiedNodeRequest:
    return VerifiedNodeRequest("node-1", "default", NOW, "nonce", key)


def provision_payload(key: str = "op-key") -> dict[str, Any]:
    return {
        "task_id": "task-1",
        "idempotency_key": key,
        "client_uuid": "client-1",
        "display_name": "vpn-1",
        "inbound_id": "1",
        "flow": None,
        "expires_at": None,
        "metadata": {"vpn_configuration_id": 7, "vpn_generation": 1},
    }


@pytest.mark.asyncio
async def test_route_replays_completed_request_without_second_runtime_call(tmp_path) -> None:
    journal = NodeOperationJournal(str(tmp_path / "journal.sqlite3"), lease_seconds=30)
    await journal.initialize()
    runtime = RuntimeSpy()
    payload = provision_payload()
    body = orjson.dumps(payload)

    first = await _execute_operation(
        request=make_request(body=body, path="/agent/clients/provision", journal=journal, runtime=runtime),
        verified=verified("op-key"),
        payload=payload,
        operation="provision",
    )
    second = await _execute_operation(
        request=make_request(body=body, path="/agent/clients/provision", journal=journal, runtime=runtime),
        verified=verified("op-key"),
        payload=payload,
        operation="provision",
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert runtime.calls == [("provision", "client-1")]


@pytest.mark.asyncio
async def test_ambiguous_provision_recovery_cleans_by_client_uuid_before_reapply(tmp_path) -> None:
    journal = NodeOperationJournal(str(tmp_path / "journal.sqlite3"), lease_seconds=30)
    await journal.initialize()
    runtime = RuntimeSpy()
    payload = provision_payload("ambiguous-key")
    body = orjson.dumps(payload)
    request_hash = sha256(b"POST\n/agent/clients/provision\n" + body).hexdigest()

    claim = await journal.claim(
        idempotency_key="ambiguous-key",
        request_hash=request_hash,
        operation="provision",
        client_uuid="client-1",
        inbound_id="1",
    )
    assert claim.owner_token
    assert await journal.mark_retryable(
        idempotency_key="ambiguous-key",
        owner_token=claim.owner_token,
        error="connection lost after possible XUI add",
    )

    result = await _execute_operation(
        request=make_request(body=body, path="/agent/clients/provision", journal=journal, runtime=runtime),
        verified=verified("ambiguous-key"),
        payload=payload,
        operation="provision",
    )

    assert result.status == "provisioned"
    assert runtime.calls == [("revoke", "client-1"), ("provision", "client-1")]


@pytest.mark.asyncio
async def test_header_body_idempotency_mismatch_is_rejected_before_runtime(tmp_path) -> None:
    journal = NodeOperationJournal(str(tmp_path / "journal.sqlite3"), lease_seconds=30)
    await journal.initialize()
    runtime = RuntimeSpy()
    payload = provision_payload("body-key")
    body = orjson.dumps(payload)

    with pytest.raises(HTTPException) as exc_info:
        await _execute_operation(
            request=make_request(body=body, path="/agent/clients/provision", journal=journal, runtime=runtime),
            verified=verified("signed-key"),
            payload=payload,
            operation="provision",
        )
    assert exc_info.value.status_code == 400
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_completed_journal_is_retired_only_by_explicit_handshake(tmp_path) -> None:
    journal = NodeOperationJournal(str(tmp_path / "journal.sqlite3"), lease_seconds=30)
    await journal.initialize()
    claim = await journal.claim(
        idempotency_key="completed",
        request_hash="hash",
        operation="revoke",
        client_uuid="client",
        inbound_id="1",
        now=NOW,
    )
    assert claim.owner_token
    assert await journal.complete(
        idempotency_key="completed",
        owner_token=claim.owner_token,
        response={"status": "revoked"},
        now=NOW,
    )

    assert await journal.retire("completed") == "retired"
    assert await journal.retire("completed") == "already_retired"


@pytest.mark.asyncio
async def test_retryable_journal_can_be_retired_but_in_progress_never_is(tmp_path) -> None:
    journal = NodeOperationJournal(str(tmp_path / "journal.sqlite3"), lease_seconds=30)
    await journal.initialize()
    retryable = await journal.claim(
        idempotency_key="retryable",
        request_hash="h1",
        operation="revoke",
        client_uuid="c1",
        inbound_id="1",
        now=NOW,
    )
    active = await journal.claim(
        idempotency_key="active",
        request_hash="h2",
        operation="revoke",
        client_uuid="c2",
        inbound_id="1",
        now=NOW,
    )
    assert retryable.owner_token and active.owner_token
    assert await journal.mark_retryable(
        idempotency_key="retryable",
        owner_token=retryable.owner_token,
        error="connection lost",
        now=NOW,
    )

    assert await journal.retire("retryable") == "retired"
    assert await journal.retire("active") == "operation_in_progress"

    # Even an expired in-progress lease must first go through ambiguity
    # recovery; retirement is not allowed to infer completion from time.
    reclaimed = await journal.claim(
        idempotency_key="active",
        request_hash="h2",
        operation="revoke",
        client_uuid="c2",
        inbound_id="1",
        now=NOW + timedelta(seconds=31),
    )
    assert reclaimed.kind is JournalClaimKind.OWNER
    assert reclaimed.reclaimed is True
    assert reclaimed.owner_token
    assert await journal.complete(
        idempotency_key="active",
        owner_token=reclaimed.owner_token,
        response={"status": "revoked"},
        now=NOW + timedelta(seconds=31),
    )
    assert await journal.retire("active") == "retired"


@pytest.mark.asyncio
async def test_retirement_route_fences_signed_key_and_maps_in_progress_to_409(tmp_path) -> None:
    journal = NodeOperationJournal(str(tmp_path / "journal.sqlite3"), lease_seconds=30)
    await journal.initialize()
    runtime = RuntimeSpy()
    request = make_request(
        body=orjson.dumps({"idempotency_key": "body-key"}),
        path="/agent/idempotency/retire",
        journal=journal,
        runtime=runtime,
    )
    with pytest.raises(HTTPException) as mismatch:
        await retire_journal(
            JournalRetireRequest(idempotency_key="body-key"),
            request,
            verified("signed-key"),
        )
    assert mismatch.value.status_code == 400

    active = await journal.claim(
        idempotency_key="active-key",
        request_hash="hash",
        operation="revoke",
        client_uuid="client",
        inbound_id="1",
        now=NOW,
    )
    assert active.owner_token
    active_request = make_request(
        body=orjson.dumps({"idempotency_key": "active-key"}),
        path="/agent/idempotency/retire",
        journal=journal,
        runtime=runtime,
    )
    with pytest.raises(HTTPException) as in_progress:
        await retire_journal(
            JournalRetireRequest(idempotency_key="active-key"),
            active_request,
            verified("active-key"),
        )
    assert in_progress.value.status_code == 409
    assert in_progress.value.detail == "operation_in_progress"


@pytest.mark.asyncio
async def test_retryable_claim_race_can_never_delete_the_new_in_progress_owner(tmp_path) -> None:
    journal = NodeOperationJournal(str(tmp_path / "journal.sqlite3"), lease_seconds=30)
    await journal.initialize()

    for index in range(20):
        key = f"race-{index}"
        initial = await journal.claim(
            idempotency_key=key,
            request_hash="hash",
            operation="provision",
            client_uuid="client",
            inbound_id="1",
            now=NOW,
        )
        assert initial.owner_token
        assert await journal.mark_retryable(
            idempotency_key=key,
            owner_token=initial.owner_token,
            error="retry",
            now=NOW,
        )

        retired, reclaimed = await asyncio.gather(
            journal.retire(key),
            journal.claim(
                idempotency_key=key,
                request_hash="hash",
                operation="provision",
                client_uuid="client",
                inbound_id="1",
                now=NOW + timedelta(seconds=1),
            ),
        )

        assert retired in {"retired", "operation_in_progress"}
        assert reclaimed.kind is JournalClaimKind.OWNER
        assert await journal.retire(key) == "operation_in_progress"


@pytest.mark.asyncio
async def test_initialize_removes_legacy_time_pruning_index(tmp_path) -> None:
    path = tmp_path / "journal.sqlite3"
    journal = NodeOperationJournal(str(path), lease_seconds=30)
    await journal.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE INDEX idx_node_operation_journal_status_updated "
            "ON node_operation_journal(status, updated_at)"
        )

    await journal.initialize()

    with sqlite3.connect(path) as connection:
        index = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_node_operation_journal_status_updated",),
        ).fetchone()
    assert index is None
