from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hmac
from typing import Mapping
from uuid import uuid4

import orjson


class NodeAuthError(ValueError):
    pass


@dataclass(slots=True)
class VerifiedNodeRequest:
    node_id: str
    key_id: str
    timestamp: datetime
    nonce: str
    idempotency_key: str


class InMemoryNonceStore:
    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._entries: dict[str, datetime] = {}

    def remember(self, key: str, *, now: datetime) -> bool:
        self._purge(now)
        expires_at = self._entries.get(key)
        if expires_at is not None and expires_at > now:
            return False
        self._entries[key] = now + timedelta(seconds=self._ttl)
        return True

    def _purge(self, now: datetime) -> None:
        expired = [key for key, expires_at in self._entries.items() if expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)


NODE_ID_HEADER = "X-Node-Id"
KEY_ID_HEADER = "X-Key-Id"
TIMESTAMP_HEADER = "X-Timestamp"
NONCE_HEADER = "X-Nonce"
IDEMPOTENCY_HEADER = "X-Idempotency-Key"
SIGNATURE_HEADER = "X-Signature"


def json_bytes(payload: object | None) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    return orjson.dumps(payload)


def body_sha256(body: bytes) -> str:
    return sha256(body).hexdigest()


def canonical_string(
    *,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    idempotency_key: str,
    body_hash: str,
) -> str:
    return "\n".join(
        [
            method.upper(),
            path,
            timestamp,
            nonce,
            idempotency_key,
            body_hash,
        ]
    )


def compute_signature(*, secret: str, canonical: str) -> str:
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), sha256).hexdigest()


def build_signed_headers(
    *,
    node_id: str,
    key_id: str,
    secret: str,
    method: str,
    path: str,
    body: bytes,
    idempotency_key: str,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    ts = timestamp or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    nonce_value = nonce or uuid4().hex
    canonical = canonical_string(
        method=method,
        path=path,
        timestamp=ts,
        nonce=nonce_value,
        idempotency_key=idempotency_key,
        body_hash=body_sha256(body),
    )
    signature = compute_signature(secret=secret, canonical=canonical)
    return {
        NODE_ID_HEADER: node_id,
        KEY_ID_HEADER: key_id,
        TIMESTAMP_HEADER: ts,
        NONCE_HEADER: nonce_value,
        IDEMPOTENCY_HEADER: idempotency_key,
        SIGNATURE_HEADER: signature,
    }


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def verify_signed_request(
    *,
    method: str,
    path: str,
    body: bytes,
    headers: Mapping[str, str],
    expected_node_id: str,
    expected_key_id: str,
    shared_secret: str,
    tolerance_seconds: int,
    nonce_store: InMemoryNonceStore,
    now: datetime | None = None,
) -> VerifiedNodeRequest:
    node_id = _header(headers, NODE_ID_HEADER)
    key_id = _header(headers, KEY_ID_HEADER)
    timestamp_raw = _header(headers, TIMESTAMP_HEADER)
    nonce = _header(headers, NONCE_HEADER)
    idempotency_key = _header(headers, IDEMPOTENCY_HEADER)
    supplied_signature = _header(headers, SIGNATURE_HEADER)

    if node_id != expected_node_id:
        raise NodeAuthError("Unexpected node id")
    if key_id != expected_key_id:
        raise NodeAuthError("Unexpected key id")
    if not timestamp_raw or not nonce or not idempotency_key or not supplied_signature:
        raise NodeAuthError("Missing authentication headers")

    current_time = now or datetime.now(UTC)
    try:
        timestamp = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NodeAuthError("Invalid timestamp") from exc
    if abs((current_time - timestamp).total_seconds()) > tolerance_seconds:
        raise NodeAuthError("Timestamp outside allowed skew")

    nonce_key = f"{node_id}:{nonce}"
    if not nonce_store.remember(nonce_key, now=current_time):
        raise NodeAuthError("Replay detected")

    canonical = canonical_string(
        method=method,
        path=path,
        timestamp=timestamp_raw,
        nonce=nonce,
        idempotency_key=idempotency_key,
        body_hash=body_sha256(body),
    )
    expected_signature = compute_signature(secret=shared_secret, canonical=canonical)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise NodeAuthError("Invalid signature")

    return VerifiedNodeRequest(
        node_id=node_id,
        key_id=key_id,
        timestamp=timestamp,
        nonce=nonce,
        idempotency_key=idempotency_key,
    )
