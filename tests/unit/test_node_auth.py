from datetime import UTC, datetime

import pytest

from shop_bot.infrastructure.nodes.auth import (
    InMemoryNonceStore,
    NodeAuthError,
    build_signed_headers,
    json_bytes,
    verify_signed_request,
)


def test_verify_signed_request_accepts_valid_signature() -> None:
    body = json_bytes({"task_id": "t1", "client_uuid": "u1"})
    headers = build_signed_headers(
        node_id="de-1",
        key_id="default",
        secret="super-secret",
        method="POST",
        path="/agent/clients/provision",
        body=body,
        idempotency_key="provision:vpn_configuration:1",
        timestamp="2026-04-25T12:00:00Z",
        nonce="nonce-1",
    )

    verified = verify_signed_request(
        method="POST",
        path="/agent/clients/provision",
        body=body,
        headers=headers,
        expected_node_id="de-1",
        expected_key_id="default",
        shared_secret="super-secret",
        tolerance_seconds=90,
        nonce_store=InMemoryNonceStore(90),
        now=datetime(2026, 4, 25, 12, 0, 30, tzinfo=UTC),
    )

    assert verified.node_id == "de-1"
    assert verified.idempotency_key == "provision:vpn_configuration:1"


def test_verify_signed_request_rejects_replay() -> None:
    body = json_bytes({"task_id": "t1"})
    headers = build_signed_headers(
        node_id="de-1",
        key_id="default",
        secret="super-secret",
        method="GET",
        path="/agent/health",
        body=body,
        idempotency_key="health:1",
        timestamp="2026-04-25T12:00:00Z",
        nonce="nonce-1",
    )
    store = InMemoryNonceStore(90)

    verify_signed_request(
        method="GET",
        path="/agent/health",
        body=body,
        headers=headers,
        expected_node_id="de-1",
        expected_key_id="default",
        shared_secret="super-secret",
        tolerance_seconds=90,
        nonce_store=store,
        now=datetime(2026, 4, 25, 12, 0, 10, tzinfo=UTC),
    )

    with pytest.raises(NodeAuthError):
        verify_signed_request(
            method="GET",
            path="/agent/health",
            body=body,
            headers=headers,
            expected_node_id="de-1",
            expected_key_id="default",
            shared_secret="super-secret",
            tolerance_seconds=90,
            nonce_store=store,
            now=datetime(2026, 4, 25, 12, 0, 20, tzinfo=UTC),
        )


def test_invalid_hmac_does_not_consume_nonce() -> None:
    body = json_bytes({"task_id": "t1"})
    good = build_signed_headers(
        node_id="de-1", key_id="default", secret="super-secret", method="POST",
        path="/agent/clients/provision", body=body, idempotency_key="op:1",
        timestamp="2026-04-25T12:00:00Z", nonce="nonce-reusable",
    )
    bad = dict(good)
    bad["X-Signature"] = "0" * 64
    store = InMemoryNonceStore(90)
    with pytest.raises(NodeAuthError):
        verify_signed_request(
            method="POST", path="/agent/clients/provision", body=body, headers=bad,
            expected_node_id="de-1", expected_key_id="default", shared_secret="super-secret",
            tolerance_seconds=90, nonce_store=store,
            now=datetime(2026, 4, 25, 12, 0, 10, tzinfo=UTC),
        )
    verified = verify_signed_request(
        method="POST", path="/agent/clients/provision", body=body, headers=good,
        expected_node_id="de-1", expected_key_id="default", shared_secret="super-secret",
        tolerance_seconds=90, nonce_store=store,
        now=datetime(2026, 4, 25, 12, 0, 10, tzinfo=UTC),
    )
    assert verified.nonce == "nonce-reusable"


def test_naive_timestamp_is_auth_error() -> None:
    body = json_bytes({"task_id": "t1"})
    headers = build_signed_headers(
        node_id="de-1", key_id="default", secret="super-secret", method="GET",
        path="/agent/health", body=body, idempotency_key="health:2",
        timestamp="2026-04-25T12:00:00", nonce="nonce-naive",
    )
    with pytest.raises(NodeAuthError, match="timezone"):
        verify_signed_request(
            method="GET", path="/agent/health", body=body, headers=headers,
            expected_node_id="de-1", expected_key_id="default", shared_secret="super-secret",
            tolerance_seconds=90, nonce_store=InMemoryNonceStore(90),
            now=datetime(2026, 4, 25, 12, 0, 10, tzinfo=UTC),
        )
