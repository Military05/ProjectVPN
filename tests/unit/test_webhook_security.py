from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from shop_bot.application.use_cases.ingest_webhook import IngestWebhook
from shop_bot.core.config import Settings
from shop_bot.core.exceptions import WebhookAuthenticationError
from shop_bot.infrastructure.payments.cryptobot import CryptoBotAdapter
from shop_bot.infrastructure.payments.heleket import HeleketAdapter
from shop_bot.infrastructure.payments.ton import TonAdapter
from shop_bot.infrastructure.payments.yookassa import YooKassaAdapter


NOW = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)


def crypto_signature(token: str, raw_body: bytes) -> str:
    key = hashlib.sha256(token.encode()).digest()
    return hmac.new(key, raw_body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_cryptobot_accepts_exact_raw_body_signature() -> None:
    token = "crypto-token"
    raw = b'{"update_type":"invoice_paid","payload":{"invoice_id":7,"status":"paid","amount":"9900","asset":"RUB","payload":"42"}}'
    adapter = CryptoBotAdapter(Settings(cryptobot_token=token))
    event = await adapter.verify_and_normalize_webhook(
        raw_body=raw,
        headers={"crypto-pay-api-signature": crypto_signature(token, raw)},
    )
    assert event.provider_payment_id == "7"
    assert event.status == "paid"
    assert event.amount_minor == 9900
    assert event.payment_order_id == 42


@pytest.mark.asyncio
async def test_cryptobot_rejects_changed_body_with_old_signature() -> None:
    token = "crypto-token"
    raw = b'{"payload":{"invoice_id":7,"status":"paid"}}'
    signature = crypto_signature(token, raw)
    adapter = CryptoBotAdapter(Settings(cryptobot_token=token))
    with pytest.raises(WebhookAuthenticationError):
        await adapter.verify_and_normalize_webhook(
            raw_body=raw + b" ",
            headers={"crypto-pay-api-signature": signature},
        )


@pytest.mark.asyncio
async def test_cryptobot_rejects_missing_signature() -> None:
    adapter = CryptoBotAdapter(Settings(cryptobot_token="crypto-token"))
    with pytest.raises(WebhookAuthenticationError):
        await adapter.verify_and_normalize_webhook(raw_body=b"{}", headers={})


def heleket_signed_body(payload: dict[str, Any], api_key: str) -> bytes:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("/", "\\/")
    encoded = base64.b64encode(canonical.encode()).decode("ascii")
    signature = hashlib.md5((encoded + api_key).encode()).hexdigest()
    signed = dict(payload)
    signed["sign"] = signature
    return json.dumps(signed, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.mark.asyncio
async def test_heleket_accepts_documented_signature_including_slashes() -> None:
    api_key = "heleket-key"
    payload = {
        "uuid": "pay-1",
        "order_id": "42",
        "status": "paid",
        "amount": "9900",
        "currency": "rub",
        "callback": "https://example.test/a/b",
    }
    adapter = HeleketAdapter(Settings(heleket_api_key=api_key))
    event = await adapter.verify_and_normalize_webhook(
        raw_body=heleket_signed_body(payload, api_key),
        headers={},
    )
    assert event.provider_payment_id == "pay-1"
    assert event.payment_order_id == 42
    assert event.amount_minor == 9900
    assert event.currency == "RUB"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_heleket_rejects_invalid_or_missing_signature(missing: bool) -> None:
    payload: dict[str, Any] = {"uuid": "pay-1", "status": "paid"}
    if not missing:
        payload["sign"] = "bad"
    adapter = HeleketAdapter(Settings(heleket_api_key="heleket-key"))
    with pytest.raises(WebhookAuthenticationError):
        await adapter.verify_and_normalize_webhook(
            raw_body=json.dumps(payload, separators=(",", ":")).encode(),
            headers={},
        )


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://api.yookassa.ru")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("failed", request=self.request, response=httpx.Response(self.status_code))

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeAsyncClient:
    response: FakeResponse

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        del args, kwargs
        return self.response


@pytest.mark.asyncio
async def test_yookassa_uses_authenticated_current_payment_not_inbound_state(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.response = FakeResponse(
        {
            "id": "yk-1",
            "status": "pending",
            "amount": {"value": "99.00", "currency": "RUB"},
            "metadata": {"payment_order_id": 42},
        }
    )
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    inbound = {"event": "payment.succeeded", "object": {"id": "yk-1", "status": "succeeded", "metadata": {"payment_order_id": 999}}}
    adapter = YooKassaAdapter(Settings(yookassa_shop_id="shop", yookassa_secret_key="secret"))
    event = await adapter.verify_and_normalize_webhook(
        raw_body=json.dumps(inbound).encode(),
        headers={},
    )
    assert event.status == "pending"
    assert event.payment_order_id == 42
    assert event.amount_minor == 9900
    assert event.payload["object"]["status"] == "pending"


@pytest.mark.asyncio
async def test_yookassa_missing_credentials_fail_closed() -> None:
    adapter = YooKassaAdapter(Settings())
    inbound = {"event": "payment.succeeded", "object": {"id": "yk-1"}}
    with pytest.raises(WebhookAuthenticationError):
        await adapter.verify_and_normalize_webhook(raw_body=json.dumps(inbound).encode(), headers={})


@pytest.mark.asyncio
async def test_ton_arbitrary_webhook_is_rejected() -> None:
    adapter = TonAdapter(Settings())
    with pytest.raises(WebhookAuthenticationError):
        await adapter.verify_and_normalize_webhook(raw_body=b'{"status":"paid"}', headers={})


class RejectingGateway:
    async def verify_and_normalize_webhook(self, *, raw_body: bytes, headers: dict[str, str]) -> Any:
        del raw_body, headers
        raise WebhookAuthenticationError("no")


class RejectingRegistry:
    def get(self, provider: str) -> RejectingGateway:
        del provider
        return RejectingGateway()


class FailingUowFactory:
    def __init__(self) -> None:
        self.called = False

    def __call__(self) -> Any:
        self.called = True
        raise AssertionError("UoW must not be opened before verification")


class QueueProbe:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue(self, job_name: str, *args: Any) -> None:
        self.jobs.append((job_name, args))


@pytest.mark.asyncio
async def test_failed_authentication_has_no_uow_or_queue_side_effects() -> None:
    uow_factory = FailingUowFactory()
    queue = QueueProbe()
    use_case = IngestWebhook(
        uow_factory=uow_factory,
        payment_registry=RejectingRegistry(),
        job_queue=queue,
        default_currency="RUB",
        clock=lambda: NOW,
    )
    with pytest.raises(WebhookAuthenticationError):
        await use_case.execute(provider="cryptobot", raw_body=b"{}", headers={})
    assert uow_factory.called is False
    assert queue.jobs == []

from shop_bot.domain.payments.models import NormalizedWebhookEvent


class VerifiedGateway:
    def __init__(self, event: NormalizedWebhookEvent) -> None:
        self.event = event

    async def verify_and_normalize_webhook(self, *, raw_body: bytes, headers: dict[str, str]) -> NormalizedWebhookEvent:
        del raw_body, headers
        return self.event


class VerifiedRegistry:
    def __init__(self, gateway: VerifiedGateway) -> None:
        self.gateway = gateway

    def get(self, provider: str) -> VerifiedGateway:
        del provider
        return self.gateway


class IngestPaymentRepository:
    def __init__(self, *, attempt: Any = None) -> None:
        self.attempt = attempt
        self.inbox_inserted = False
        self.created_event_kwargs: list[dict[str, Any]] = []
        self.inbox_status_calls: list[tuple[int, str]] = []

    async def save_webhook_inbox(self, **kwargs: Any) -> tuple[bool, dict[str, Any]]:
        del kwargs
        inserted = not self.inbox_inserted
        self.inbox_inserted = True
        return inserted, {"webhook_inbox_id": 1}

    async def get_attempt_entity_by_provider_payment_id(self, provider: str, provider_payment_id: str) -> Any:
        del provider, provider_payment_id
        return self.attempt

    async def create_payment_event(self, **kwargs: Any) -> dict[str, Any]:
        self.created_event_kwargs.append(kwargs)
        return {"payment_event_id": 5}

    async def mark_webhook_inbox_status(self, webhook_inbox_id: int, status: str, **kwargs: Any) -> None:
        del kwargs
        self.inbox_status_calls.append((webhook_inbox_id, status))


class IngestUow:
    def __init__(self, payments: IngestPaymentRepository) -> None:
        self.payments = payments

    async def __aenter__(self) -> "IngestUow":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_verified_event_ingests_once_and_duplicate_is_only_requeued() -> None:
    normalized = NormalizedWebhookEvent(
        provider="cryptobot",
        event_key="evt-1",
        event_type="invoice_paid",
        status="paid",
        occurred_at=NOW,
        provider_payment_id="pay-1",
        payment_order_id=999,
        amount_minor=9900,
        currency="rub",
        payload={"verified": True},
    )
    payments = IngestPaymentRepository(attempt=None)
    queue = QueueProbe()
    use_case = IngestWebhook(
        uow_factory=lambda: IngestUow(payments),
        payment_registry=VerifiedRegistry(VerifiedGateway(normalized)),
        job_queue=queue,
        default_currency="RUB",
        clock=lambda: NOW,
    )
    raw = b'{"caller":"untrusted-order-999"}'

    first = await use_case.execute(provider="cryptobot", raw_body=raw, headers={})
    second = await use_case.execute(provider="cryptobot", raw_body=raw, headers={})

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert payments.created_event_kwargs[0]["payment_order_id"] is None
    assert payments.created_event_kwargs[0]["reported_payment_order_id"] == 999
    assert payments.created_event_kwargs[0]["amount_minor"] == 9900
    assert payments.created_event_kwargs[0]["currency"] == "RUB"
    assert queue.jobs == [
        ("process_payment_event", (5,)),
        ("process_payment_event", (5,)),
    ]


@pytest.mark.asyncio
async def test_yookassa_disallowed_source_rejected_before_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from starlette.requests import Request
    from fastapi import HTTPException
    from shop_bot.apps.api.routes import webhooks as webhook_routes

    called = False
    async def forbidden_ingest(*args: Any, **kwargs: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("provider ingest must not run")
    monkeypatch.setattr(webhook_routes, "ingest_webhook_event", forbidden_ingest)
    body = b'{"event":"payment.succeeded","object":{"id":"yk-1"}}'
    sent = False
    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}
    app = SimpleNamespace(state=SimpleNamespace(settings=Settings(
        yookassa_webhook_allowed_networks=["185.71.76.0/27"], trusted_proxy_cidrs=[]
    )))
    request = Request({
        "type":"http", "http_version":"1.1", "method":"POST", "scheme":"https",
        "path":"/yookassa/webhook", "raw_path":b"/yookassa/webhook", "query_string":b"",
        "headers":[], "server":("test",443), "client":("203.0.113.10",1234), "app":app,
    }, receive)
    with pytest.raises(HTTPException) as exc:
        await webhook_routes._handle_payment_webhook(provider="yookassa", request=request, container=object())
    assert exc.value.status_code == 401
    assert called is False


def test_yookassa_effective_ip_only_trusts_forwarded_chain_from_trusted_proxy() -> None:
    from types import SimpleNamespace
    from starlette.requests import Request
    from shop_bot.apps.api.routes.webhooks import _effective_client_ip

    def req(peer: str, xff: str | None) -> Request:
        headers = [] if xff is None else [(b"x-forwarded-for", xff.encode())]
        return Request({
            "type":"http", "http_version":"1.1", "method":"POST", "scheme":"https",
            "path":"/", "raw_path":b"/", "query_string":b"", "headers":headers,
            "server":("test",443), "client":(peer,1), "app":SimpleNamespace(state=SimpleNamespace()),
        })

    assert _effective_client_ip(req("203.0.113.10", "185.71.76.5"), ["10.0.0.0/8"]) == "203.0.113.10"
    assert _effective_client_ip(req("10.0.0.2", "185.71.76.5, 10.0.0.1"), ["10.0.0.0/8"]) == "185.71.76.5"


@pytest.mark.asyncio
async def test_webhook_stream_body_limit_applies_without_content_length() -> None:
    from types import SimpleNamespace
    from starlette.requests import Request
    from fastapi import HTTPException
    from shop_bot.apps.api.routes.webhooks import _read_bounded_body

    chunks = iter([b"1234", b"5678"])
    async def receive() -> dict[str, Any]:
        try:
            chunk = next(chunks)
        except StopIteration:
            return {"type":"http.request", "body":b"", "more_body":False}
        return {"type":"http.request", "body":chunk, "more_body":True}
    request = Request({
        "type":"http", "http_version":"1.1", "method":"POST", "scheme":"https", "path":"/",
        "raw_path":b"/", "query_string":b"", "headers":[], "server":("test",443),
        "client":("127.0.0.1",1), "app":SimpleNamespace(state=SimpleNamespace()),
    }, receive)
    with pytest.raises(HTTPException) as exc:
        await _read_bounded_body(request, 6)
    assert exc.value.status_code == 413
