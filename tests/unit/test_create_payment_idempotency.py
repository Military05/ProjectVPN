from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from shop_bot.application.use_cases.create_payment import CreatePayment
from shop_bot.core.exceptions import ConflictError
from shop_bot.domain.entities.payment import PaymentAttempt, PaymentOrder, PaymentStatus
from shop_bot.domain.entities.tariff import Tariff
from shop_bot.domain.entities.user import User
from shop_bot.domain.payments.models import PaymentIntent
from shop_bot.domain.value_objects.money import Money


NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


class SharedPaymentState:
    def __init__(self) -> None:
        self.orders: dict[str, PaymentOrder] = {}
        self.orders_by_id: dict[int, PaymentOrder] = {}
        self.attempts: dict[int, PaymentAttempt] = {}
        self.attempts_by_order: dict[int, list[int]] = {}
        self.transactions: list[dict[str, Any]] = []
        self.next_order_id = 1
        self.next_attempt_id = 1
        self.insert_lock = asyncio.Lock()
        self.order_locks: dict[int, asyncio.Lock] = {}


class FakeUsers:
    def __init__(self, user_id_by_telegram: dict[int, int]) -> None:
        self.user_id_by_telegram = user_id_by_telegram

    async def get_entity_by_contact(self, contact_type: str, value: str) -> User | None:
        assert contact_type == "telegram_id"
        user_id = self.user_id_by_telegram.get(int(value))
        return User(id=user_id, name_or_nick=f"u-{value}") if user_id is not None else None

    async def register_telegram_entity(
        self, telegram_id: int, username: str | None, name_or_nick: str
    ) -> User:
        del username
        user_id = self.user_id_by_telegram.setdefault(telegram_id, max(self.user_id_by_telegram.values(), default=0) + 1)
        return User(id=user_id, name_or_nick=name_or_nick)


class FakeAdmin:
    def __init__(self, tariffs: dict[int, Tariff]) -> None:
        self.tariffs = tariffs

    async def get_tariff_entity(self, tariff_id: int) -> Tariff | None:
        return self.tariffs.get(tariff_id)


class FakePayments:
    def __init__(self, state: SharedPaymentState, uow: "FakeUow") -> None:
        self.state = state
        self.uow = uow

    async def add_order_entity(self, order: PaymentOrder) -> PaymentOrder:
        async with self.state.insert_lock:
            existing = self.state.orders.get(order.idempotency_key)
            if existing is not None:
                return existing
            order.id = self.state.next_order_id
            self.state.next_order_id += 1
            self.state.orders[order.idempotency_key] = order
            self.state.orders_by_id[order.id] = order
            self.state.order_locks[order.id] = asyncio.Lock()
            return order

    async def get_order_entity(self, order_id: int, *, for_update: bool = False) -> PaymentOrder | None:
        if for_update:
            lock = self.state.order_locks[order_id]
            await lock.acquire()
            self.uow.acquired.append(lock)
        return self.state.orders_by_id.get(order_id)

    async def get_latest_attempt_entity(self, order_id: int) -> PaymentAttempt | None:
        ids = self.state.attempts_by_order.get(order_id, [])
        return self.state.attempts[ids[-1]] if ids else None

    async def add_attempt_entity(self, attempt: PaymentAttempt) -> PaymentAttempt:
        # Mirrors the partial unique index: only one CREATED claim per order.
        for attempt_id in self.state.attempts_by_order.get(attempt.payment_order_id, []):
            current = self.state.attempts[attempt_id]
            if current.status is PaymentStatus.CREATED and attempt.status is PaymentStatus.CREATED:
                raise AssertionError("duplicate durable creation claim")
        attempt.id = self.state.next_attempt_id
        self.state.next_attempt_id += 1
        self.state.attempts[attempt.id] = attempt
        self.state.attempts_by_order.setdefault(attempt.payment_order_id, []).append(attempt.id)
        return attempt

    async def get_attempt_entity(self, attempt_id: int, *, for_update: bool = False) -> PaymentAttempt | None:
        del for_update
        return self.state.attempts.get(attempt_id)

    async def save_attempt_entity(self, attempt: PaymentAttempt) -> None:
        assert attempt.id is not None
        self.state.attempts[attempt.id] = attempt

    async def create_provider_transaction(self, **payload: Any) -> None:
        key = (payload["provider"], payload["provider_transaction_key"])
        if all((item["provider"], item["provider_transaction_key"]) != key for item in self.state.transactions):
            self.state.transactions.append(payload)


class FakeUow:
    def __init__(
        self,
        state: SharedPaymentState,
        users: FakeUsers,
        admin: FakeAdmin,
    ) -> None:
        self.acquired: list[asyncio.Lock] = []
        self.users = users
        self.admin = admin
        self.payments = FakePayments(state, self)

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for lock in reversed(self.acquired):
            if lock.locked():
                lock.release()


class UowFactory:
    def __init__(self, state: SharedPaymentState, users: FakeUsers, admin: FakeAdmin) -> None:
        self.state = state
        self.users = users
        self.admin = admin

    def __call__(self) -> FakeUow:
        return FakeUow(self.state, self.users, self.admin)


class Gateway:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def create_payment(self, *, order: dict[str, Any], return_url: str) -> PaymentIntent:
        del return_url
        self.calls += 1
        self.started.set()
        if self.block:
            await self.release.wait()
        return PaymentIntent(
            provider=str(order["provider"]),
            provider_payment_id=f"remote-{order['payment_order_id']}",
            status="pending",
            payment_url="https://pay.example/invoice",
            payload={"id": f"remote-{order['payment_order_id']}"},
        )


class Registry:
    def __init__(self, gateways: dict[str, Gateway]) -> None:
        self.gateways = gateways
        self.requested: list[str] = []

    def get(self, provider: str) -> Gateway:
        self.requested.append(provider)
        return self.gateways[provider]


class Queue:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, tuple[Any, ...]]] = []

    async def enqueue(self, job_name: Any, *args: Any) -> None:
        self.jobs.append((job_name, args))


def make_use_case(
    *, state: SharedPaymentState | None = None, tariffs: dict[int, Tariff] | None = None, users: dict[int, int] | None = None
) -> tuple[CreatePayment, SharedPaymentState, Gateway, Registry]:
    state = state or SharedPaymentState()
    tariff_map = tariffs or {1: Tariff(id=1, name="Main", price=Money(9900, "RUB"), period_days=30)}
    user_repo = FakeUsers(users or {100: 10, 200: 20})
    gateway = Gateway()
    registry = Registry({"yookassa": gateway, "dummy": Gateway()})
    use_case = CreatePayment(
        uow_factory=UowFactory(state, user_repo, FakeAdmin(tariff_map)),
        payment_registry=registry,
        job_queue=Queue(),
        default_provider="yookassa",
        return_url="https://merchant.example/return",
        creation_lease_seconds=30,
        clock=lambda: NOW,
    )
    return use_case, state, gateway, registry


@pytest.mark.asyncio
async def test_same_key_same_request_reuses_finalized_attempt_without_gateway_call() -> None:
    use_case, _, gateway, _ = make_use_case()
    first = await use_case.execute(telegram_id=100, tariff_id=1, idempotency_key="same")
    second = await use_case.execute(telegram_id=100, tariff_id=1, idempotency_key="same")
    assert first["payment_order_id"] == second["payment_order_id"]
    assert first["provider_payment_id"] == second["provider_payment_id"]
    assert gateway.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"telegram_id": 100, "tariff_id": 1, "provider": "yookassa"}, {"telegram_id": 200, "tariff_id": 1, "provider": "yookassa"}),
        ({"telegram_id": 100, "tariff_id": 1, "provider": "yookassa"}, {"telegram_id": 100, "tariff_id": 2, "provider": "yookassa"}),
        ({"telegram_id": 100, "tariff_id": 1, "provider": "yookassa"}, {"telegram_id": 100, "tariff_id": 1, "provider": "dummy"}),
    ],
)
async def test_same_key_different_request_conflicts_before_second_external_call(
    first: dict[str, Any], second: dict[str, Any]
) -> None:
    tariffs = {
        1: Tariff(id=1, name="Main", price=Money(9900, "RUB"), period_days=30),
        2: Tariff(id=2, name="Other", price=Money(11900, "RUB"), period_days=60),
    }
    use_case, _, gateway, registry = make_use_case(tariffs=tariffs)
    await use_case.execute(**first, idempotency_key="conflict")
    calls_before = sum(item.calls for item in registry.gateways.values())
    with pytest.raises(ConflictError):
        await use_case.execute(**second, idempotency_key="conflict")
    assert sum(item.calls for item in registry.gateways.values()) == calls_before
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_identity_helper_rejects_amount_currency_and_period_mismatch() -> None:
    base = PaymentOrder(None, 1, 1, "yookassa", PaymentStatus.PENDING, Money(9900, "RUB"), 30, "key")
    variants = [
        PaymentOrder(None, 1, 1, "yookassa", PaymentStatus.PENDING, Money(9800, "RUB"), 30, "key"),
        PaymentOrder(None, 1, 1, "yookassa", PaymentStatus.PENDING, Money(9900, "USD"), 30, "key"),
        PaymentOrder(None, 1, 1, "yookassa", PaymentStatus.PENDING, Money(9900, "RUB"), 60, "key"),
    ]
    assert all(not base.has_same_creation_identity(candidate) for candidate in variants)


@pytest.mark.asyncio
async def test_concurrent_same_request_has_exactly_one_external_invoice_creation() -> None:
    use_case, _, gateway, _ = make_use_case()
    gateway.block = True
    first = asyncio.create_task(use_case.execute(telegram_id=100, tariff_id=1, idempotency_key="race"))
    await gateway.started.wait()
    second = asyncio.create_task(use_case.execute(telegram_id=100, tariff_id=1, idempotency_key="race"))
    second_result = await asyncio.wait_for(second, timeout=1)
    assert second_result["provider_payment_id"] is None
    assert gateway.calls == 1
    gateway.release.set()
    first_result = await first
    assert first_result["payment_order_id"] == second_result["payment_order_id"]
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_stale_creation_claim_can_be_reclaimed_and_old_token_is_fenced() -> None:
    use_case, state, gateway, _ = make_use_case()
    prepared = await use_case._prepare_order(
        telegram_id=100,
        tariff_id=1,
        provider="yookassa",
        idempotency_key="stale",
        username=None,
        name_or_nick=None,
    )
    old_attempt = prepared.attempt
    old_token = prepared.creation_lease_token
    assert old_token is not None
    old_attempt.creation_lease_expires_at = NOW - timedelta(seconds=1)

    reclaimed = await use_case._prepare_order(
        telegram_id=100,
        tariff_id=1,
        provider="yookassa",
        idempotency_key="stale",
        username=None,
        name_or_nick=None,
    )
    assert reclaimed.owns_creation_claim is True
    assert reclaimed.attempt.id != old_attempt.id
    assert old_attempt.status is PaymentStatus.FAILED

    intent = PaymentIntent("yookassa", "late-remote", "pending", None, {})
    current = await use_case._finalize_owned_claim(
        order=prepared.order,
        payment_attempt_id=old_attempt.id,
        lease_token=old_token,
        intent=intent,
    )
    assert current.id == reclaimed.attempt.id
    assert current.provider_payment_id is None
    assert gateway.calls == 0
    assert len(state.transactions) == 0

@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["amount", "currency", "period"])
async def test_persisted_amount_currency_or_period_drift_conflicts_before_external_call(field: str) -> None:
    use_case, state, gateway, _ = make_use_case()
    await use_case.execute(telegram_id=100, tariff_id=1, idempotency_key=f"drift-{field}")
    order = state.orders[f"drift-{field}"]
    if field == "amount":
        order.amount = Money(9800, "RUB")
    elif field == "currency":
        order.amount = Money(9900, "USD")
    else:
        order.requested_period_days = 31
    calls_before = gateway.calls
    with pytest.raises(ConflictError):
        await use_case.execute(telegram_id=100, tariff_id=1, idempotency_key=f"drift-{field}")
    assert gateway.calls == calls_before


@pytest.mark.asyncio
async def test_provider_exception_marks_owned_claim_failed_and_retryable() -> None:
    use_case, state, gateway, _ = make_use_case()

    async def fail_create(*, order: dict[str, Any], return_url: str) -> PaymentIntent:
        del order, return_url
        gateway.calls += 1
        raise RuntimeError("provider unavailable")

    gateway.create_payment = fail_create  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await use_case.execute(telegram_id=100, tariff_id=1, idempotency_key="provider-fail")
    attempts = [state.attempts[item] for item in state.attempts_by_order[1]]
    assert attempts[-1].status is PaymentStatus.FAILED
    assert attempts[-1].creation_lease_token is None
    assert attempts[-1].payload["creation_error"] == "provider_create_failed"
