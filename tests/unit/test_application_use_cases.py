from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from shop_bot.application.use_cases.activate_subscription import ActivateSubscription
from shop_bot.application.use_cases.process_payment import ProcessPayment
from shop_bot.domain.entities.payment import (
    PaymentAttempt,
    PaymentEvent,
    PaymentEventStatus,
    PaymentOrder,
    PaymentStatus,
)
from shop_bot.domain.entities.subscription import Subscription, SubscriptionPeriod
from shop_bot.domain.services.subscription_policy import SubscriptionPolicy
from shop_bot.domain.value_objects.money import Money


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class FakeSubscriptionRepository:
    def __init__(self) -> None:
        self.active: Subscription | None = None
        self.last_period: SubscriptionPeriod | None = None
        self.saved: list[Subscription] = []
        self.periods: list[SubscriptionPeriod] = []

    async def get_active_entity_for_user(
        self,
        user_id: int,
        *,
        for_update: bool = False,
    ) -> Subscription | None:
        return self.active

    async def get_last_period_entity(
        self,
        subscription_id: int,
        *,
        for_update: bool = False,
    ) -> SubscriptionPeriod | None:
        return self.last_period

    async def add_entity(self, subscription: Subscription) -> Subscription:
        subscription.id = 101
        self.active = subscription
        return subscription

    async def save_entity(self, subscription: Subscription) -> None:
        self.saved.append(subscription)
        self.active = subscription

    async def add_period_entity(self, period: SubscriptionPeriod) -> SubscriptionPeriod:
        period.id = 201
        self.periods.append(period)
        self.last_period = period
        return period


class FakePaymentRepository:
    def __init__(self) -> None:
        self.event = PaymentEvent(
            id=11,
            payment_order_id=22,
            payment_attempt_id=33,
            provider="dummy",
            event_type="payment.succeeded",
            event_key="evt-paid-11",
            status=PaymentEventStatus.RECEIVED,
            payload={"status": "paid", "provider_payment_id": "dummy-22"},
            occurred_at=NOW,
            provider_payment_id="dummy-22",
            reported_payment_order_id=22,
            payment_status=PaymentStatus.PAID,
            amount_minor=9900,
            currency="RUB",
        )
        self.order = PaymentOrder(
            id=22,
            user_id=7,
            tariff_id=8,
            provider="dummy",
            status=PaymentStatus.PENDING,
            amount=Money(9900, "RUB"),
            requested_period_days=30,
            idempotency_key="order-22",
        )
        self.attempt = PaymentAttempt(
            id=33,
            payment_order_id=22,
            provider="dummy",
            status=PaymentStatus.PENDING,
            provider_payment_id="dummy-22",
        )
        self.transactions: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []

    async def get_event_entity(self, event_id: int, *, for_update: bool = False) -> PaymentEvent | None:
        return self.event if event_id == self.event.id else None

    async def get_attempt_entity(self, attempt_id: int) -> PaymentAttempt | None:
        return self.attempt if attempt_id == self.attempt.id else None

    async def get_latest_attempt_entity(self, order_id: int) -> PaymentAttempt | None:
        return self.attempt if order_id == self.order.id else None

    async def get_order_entity(self, order_id: int, *, for_update: bool = False) -> PaymentOrder | None:
        return self.order if order_id == self.order.id else None

    async def save_event_entity(self, event: PaymentEvent) -> None:
        self.event = event

    async def save_attempt_entity(self, attempt: PaymentAttempt) -> None:
        self.attempt = attempt

    async def save_order_entity(self, order: PaymentOrder) -> None:
        self.order = order

    async def create_provider_transaction(self, **payload: Any) -> None:
        self.transactions.append(payload)

    async def create_outbox_event(self, **payload: Any) -> int:
        self.outbox.append(payload)
        return len(self.outbox)


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.payments = FakePaymentRepository()
        self.subscriptions = FakeSubscriptionRepository()

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeJobQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue(self, job_name: str, *args: Any) -> None:
        self.jobs.append((job_name, args))


@pytest.mark.asyncio
async def test_activate_subscription_persists_entity_and_period() -> None:
    repository = FakeSubscriptionRepository()
    use_case = ActivateSubscription(SubscriptionPolicy())

    activation = await use_case.execute(
        repository=repository,
        user_id=7,
        tariff_id=8,
        period_days=30,
        now=NOW,
    )

    assert activation.subscription.id == 101
    assert activation.period.id == 201
    assert activation.period.subscription_id == 101
    assert repository.periods == [activation.period]


@pytest.mark.asyncio
async def test_paid_payment_activates_subscription_and_queues_vpn() -> None:
    uow = FakeUnitOfWork()
    queue = FakeJobQueue()
    use_case = ProcessPayment(
        uow_factory=lambda: uow,
        activate_subscription=ActivateSubscription(SubscriptionPolicy()),
        job_queue=queue,
        clock=lambda: NOW,
    )

    result = await use_case.execute(payment_event_id=11)

    assert result == {"status": "processed", "subscription_id": 101}
    assert uow.payments.order.status is PaymentStatus.PAID
    assert uow.payments.attempt.status is PaymentStatus.PAID
    assert uow.payments.event.status is PaymentEventStatus.PROCESSED
    assert uow.payments.transactions[0]["transaction_status"] == "paid"
    assert uow.payments.transactions[0]["amount_minor"] == 9900
    assert uow.payments.transactions[0]["currency"] == "RUB"
    assert uow.payments.outbox[0]["event_name"] == "subscription_activated"
    assert queue.jobs == [
        ("provision_subscription", (101,)),
        ("publish_outbox", ()),
    ]


@pytest.mark.asyncio
async def test_processed_payment_event_is_idempotent() -> None:
    uow = FakeUnitOfWork()
    uow.payments.event.status = PaymentEventStatus.PROCESSED
    queue = FakeJobQueue()
    use_case = ProcessPayment(
        uow_factory=lambda: uow,
        activate_subscription=ActivateSubscription(SubscriptionPolicy()),
        job_queue=queue,
        clock=lambda: NOW,
    )

    result = await use_case.execute(payment_event_id=11)

    assert result == {"status": "processed"}
    assert queue.jobs == []

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        lambda repo: setattr(repo.event, "amount_minor", 9899),
        lambda repo: setattr(repo.event, "currency", "USD"),
        lambda repo: setattr(repo.event, "provider_payment_id", "wrong-payment"),
        lambda repo: setattr(repo.event, "reported_payment_order_id", 999),
        lambda repo: setattr(repo.event, "provider", "cryptobot"),
    ],
)
async def test_paid_payment_invariant_failure_has_no_success_side_effects(mutation) -> None:
    uow = FakeUnitOfWork()
    mutation(uow.payments)
    queue = FakeJobQueue()
    use_case = ProcessPayment(
        uow_factory=lambda: uow,
        activate_subscription=ActivateSubscription(SubscriptionPolicy()),
        job_queue=queue,
        clock=lambda: NOW,
    )

    result = await use_case.execute(payment_event_id=11)

    assert result == {"status": "rejected"}
    assert uow.payments.order.status is PaymentStatus.PENDING
    assert uow.payments.attempt.status is PaymentStatus.PENDING
    assert uow.payments.event.status is PaymentEventStatus.FAILED
    assert uow.subscriptions.active is None
    assert uow.payments.transactions == []
    assert uow.payments.outbox == []
    assert queue.jobs == []


@pytest.mark.asyncio
async def test_paid_payment_unknown_exact_attempt_cannot_mark_order_paid() -> None:
    uow = FakeUnitOfWork()
    uow.payments.event.payment_attempt_id = 999
    queue = FakeJobQueue()
    use_case = ProcessPayment(
        uow_factory=lambda: uow,
        activate_subscription=ActivateSubscription(SubscriptionPolicy()),
        job_queue=queue,
        clock=lambda: NOW,
    )

    result = await use_case.execute(payment_event_id=11)

    assert result == {"status": "rejected"}
    assert uow.payments.order.status is PaymentStatus.PENDING
    assert uow.payments.attempt.status is PaymentStatus.PENDING
    assert uow.payments.event.status is PaymentEventStatus.FAILED
    assert queue.jobs == []


@pytest.mark.asyncio
async def test_duplicate_valid_paid_event_does_not_activate_twice() -> None:
    uow = FakeUnitOfWork()
    queue = FakeJobQueue()
    use_case = ProcessPayment(
        uow_factory=lambda: uow,
        activate_subscription=ActivateSubscription(SubscriptionPolicy()),
        job_queue=queue,
        clock=lambda: NOW,
    )

    first = await use_case.execute(payment_event_id=11)
    second = await use_case.execute(payment_event_id=11)

    assert first == {"status": "processed", "subscription_id": 101}
    assert second == {"status": "processed"}
    assert len(uow.subscriptions.periods) == 1
    assert len(uow.payments.transactions) == 1
    assert len(uow.payments.outbox) == 1
