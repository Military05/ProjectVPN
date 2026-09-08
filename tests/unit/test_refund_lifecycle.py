from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from shop_bot.application.job_names import JobName
from shop_bot.application.use_cases.process_payment import ProcessPayment
from shop_bot.core.config import Settings
from shop_bot.domain.entities.payment import PaymentAttempt, PaymentEvent, PaymentEventStatus, PaymentOrder, PaymentStatus
from shop_bot.domain.entities.subscription import Subscription, SubscriptionPeriod, SubscriptionStatus
from shop_bot.domain.entities.vpn import VpnConfiguration, VpnConfigurationStatus
from shop_bot.domain.value_objects.money import Money
from shop_bot.infrastructure.payments import yookassa as yookassa_module
from shop_bot.infrastructure.payments.yookassa import YooKassaAdapter

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class Response:
    def __init__(self, payload: dict[str, Any]) -> None: self.payload = payload
    def raise_for_status(self) -> None: return None
    def json(self) -> dict[str, Any]: return self.payload


class Client:
    payload: dict[str, Any] = {}
    def __init__(self, *args: Any, **kwargs: Any) -> None: pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args: Any): return None
    async def get(self, url: str, **kwargs: Any):
        assert "/v3/refunds/ref-1" in url
        return Response(self.payload)


@pytest.mark.asyncio
async def test_yookassa_refund_is_normalized_as_separate_financial_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yookassa_module.httpx, "AsyncClient", Client)
    Client.payload = {
        "id": "ref-1", "status": "succeeded", "payment_id": "pay-1",
        "amount": {"value": "10.50", "currency": "RUB"}, "created_at": "2026-08-20T12:00:00Z",
    }
    event = await YooKassaAdapter(Settings(yookassa_shop_id="shop", yookassa_secret_key="secret")).verify_and_normalize_webhook(
        raw_body=json.dumps({"event":"refund.succeeded", "object":{"id":"ref-1"}}).encode(), headers={}
    )
    assert event.event_key == "yookassa:refund:ref-1:succeeded"
    assert event.event_type == "refund.succeeded"
    assert event.status is None
    assert event.provider_payment_id == "pay-1"
    assert event.amount_minor == 1050


class Queue:
    def __init__(self): self.jobs: list[tuple[Any, tuple[Any, ...]]] = []
    async def enqueue(self, name: Any, *args: Any) -> None: self.jobs.append((name, args))


class Payments:
    def __init__(self, event: PaymentEvent, attempt: PaymentAttempt, order: PaymentOrder, cumulative: int):
        self.event, self.attempt, self.order, self.cumulative = event, attempt, order, cumulative
        self.transactions: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
    async def get_event_entity(self, event_id: int, for_update: bool=False): return self.event
    async def get_attempt_entity(self, attempt_id: int): return self.attempt
    async def get_order_entity(self, order_id: int, for_update: bool=False): return self.order
    async def create_provider_transaction(self, **kwargs: Any): self.transactions.append(kwargs)
    async def sum_provider_transactions(self, **kwargs: Any): return self.cumulative
    async def save_event_entity(self, event: PaymentEvent): self.event = event
    async def create_outbox_event(self, **kwargs: Any): self.outbox.append(kwargs); return len(self.outbox)


class Subs:
    def __init__(self, period: SubscriptionPeriod | None, subscription: Subscription, *, funded_after: bool):
        self.period, self.subscription, self.funded_after = period, subscription, funded_after
    async def get_period_by_payment_order_id(self, order_id: int, for_update: bool=False): return self.period
    async def save_period_entity(self, period: SubscriptionPeriod): self.period = period
    async def get_entity(self, sub_id: int, for_update: bool=False): return self.subscription
    async def get_current_period_entity(self, sub_id: int, now: datetime):
        return self.period if self.period is not None and self.period.contains(now) else None
    async def has_funded_period_after(self, sub_id: int, now: datetime): return self.funded_after
    async def save_entity(self, subscription: Subscription): self.subscription = subscription


class Vpn:
    def __init__(self, config: VpnConfiguration | None): self.config = config
    async def get_active_entity_for_subscription(self, sub_id: int): return self.config


class Uow:
    def __init__(self, payments: Payments, subs: Subs, vpn: Vpn): self.payments, self.subscriptions, self.vpn = payments, subs, vpn
    async def __aenter__(self): return self
    async def __aexit__(self, *args: Any): return None


def scenario(*, refund_minor: int, cumulative: int, period: SubscriptionPeriod | None = None, funded_after: bool = False):
    event = PaymentEvent(id=1, payment_order_id=1, payment_attempt_id=2, provider="yookassa", event_type="refund.succeeded",
        event_key=f"refund-{refund_minor}-{cumulative}", status=PaymentEventStatus.RECEIVED, payload={}, occurred_at=NOW,
        provider_payment_id="pay-1", amount_minor=refund_minor, currency="RUB")
    attempt = PaymentAttempt(id=2, payment_order_id=1, provider="yookassa", status=PaymentStatus.PAID, provider_payment_id="pay-1")
    order = PaymentOrder(id=1, user_id=1, tariff_id=1, provider="yookassa", status=PaymentStatus.PAID,
        amount=Money(1000, "RUB"), requested_period_days=30, idempotency_key="o1")
    subscription = Subscription(id=3, user_id=1, tariff_id=1, status=SubscriptionStatus.ACTIVE, created_at=NOW-timedelta(days=1))
    if period is None and cumulative >= 1000:
        period = SubscriptionPeriod(id=4, subscription_id=3, starts_at=NOW-timedelta(days=1), expires_at=NOW+timedelta(days=29), is_paid=True, created_at=NOW-timedelta(days=1), payment_order_id=1)
    vpn = VpnConfiguration(id=9, subscription_id=3, server_endpoint_id=1, client_uuid=__import__('uuid').UUID('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'), display_name='vpn', status=VpnConfigurationStatus.ACTIVE)
    payments = Payments(event, attempt, order, cumulative)
    subs = Subs(period, subscription, funded_after=funded_after)
    queue = Queue()
    case = ProcessPayment(uow_factory=lambda: Uow(payments, subs, Vpn(vpn)), activate_subscription=SimpleNamespace(), job_queue=queue, clock=lambda: NOW)
    return case, payments, subs, queue


@pytest.mark.asyncio
async def test_partial_refund_records_transaction_without_revoking_access() -> None:
    case, payments, subs, queue = scenario(refund_minor=400, cumulative=400)
    result = await case.execute(payment_event_id=1)
    assert result == {"status":"processed", "subscription_id":None}
    assert payments.transactions[0]["transaction_type"] == "refund_succeeded"
    assert subs.subscription.status is SubscriptionStatus.ACTIVE
    assert queue.jobs == [(JobName.PUBLISH_OUTBOX, ())]


@pytest.mark.asyncio
async def test_full_refund_unfunds_period_ends_subscription_and_queues_vpn_revoke() -> None:
    case, payments, subs, queue = scenario(refund_minor=600, cumulative=1000)
    result = await case.execute(payment_event_id=1)
    assert result == {"status":"processed", "subscription_id":None}
    assert subs.period is not None and subs.period.is_paid is False
    assert subs.subscription.status is SubscriptionStatus.ENDED
    assert (JobName.REVOKE_VPN_CONFIGURATION, (9, "expiration")) in queue.jobs
    assert (JobName.PUBLISH_OUTBOX, ()) in queue.jobs


@pytest.mark.asyncio
async def test_full_refund_historical_unmapped_period_emits_reconciliation_outbox() -> None:
    # Explicitly use historical NULL provenance by removing the auto-created linked period.
    case, payments, subs, queue = scenario(refund_minor=1000, cumulative=1000)
    subs.period = None
    await case.execute(payment_event_id=1)
    assert [e["event_name"] for e in payments.outbox] == ["refund_entitlement_reconciliation_required"]
    assert all(job[0] is not JobName.REVOKE_VPN_CONFIGURATION for job in queue.jobs)
