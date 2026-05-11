from datetime import UTC, datetime, timedelta

import pytest

from shop_bot.domain.subscriptions.service import SubscriptionService


class FakeSubscriptionRepo:
    def __init__(self, *, active_subscription=None, last_period=None):
        self.active_subscription = active_subscription
        self.last_period = last_period
        self.created_subscriptions = []
        self.ended_subscriptions = []
        self.created_periods = []
        self._next_subscription_id = 100
        self._next_period_id = 500

    async def lock_active_subscription_for_user(self, user_id: int):
        return self.active_subscription

    async def create_subscription(self, *, user_id: int, tariff_id: int, status: str, created_at: datetime) -> int:
        self._next_subscription_id += 1
        record = {
            "subscription_id": self._next_subscription_id,
            "user_id": user_id,
            "tariff_id": tariff_id,
            "status": status,
            "created_at": created_at,
        }
        self.active_subscription = record
        self.created_subscriptions.append(record)
        return self._next_subscription_id

    async def end_subscription(self, subscription_id: int, *, ended_at: datetime, status: str) -> None:
        self.ended_subscriptions.append(
            {
                "subscription_id": subscription_id,
                "ended_at": ended_at,
                "status": status,
            }
        )

    async def lock_last_period(self, subscription_id: int):
        return self.last_period

    async def create_period(
        self,
        *,
        subscription_id: int,
        starts_at: datetime,
        expires_at: datetime,
        is_paid: bool,
        created_at: datetime,
    ) -> int:
        self._next_period_id += 1
        record = {
            "subscription_id": subscription_id,
            "starts_at": starts_at,
            "expires_at": expires_at,
            "is_paid": is_paid,
            "created_at": created_at,
        }
        self.created_periods.append(record)
        return self._next_period_id


@pytest.mark.asyncio
async def test_apply_paid_period_creates_new_subscription_when_absent() -> None:
    service = SubscriptionService()
    repo = FakeSubscriptionRepo()
    now = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)

    result = await service.apply_paid_period(
        subscription_repo=repo,
        user_id=10,
        tariff_id=3,
        period_days=30,
        now=now,
    )

    assert result.created_new_subscription is True
    assert result.starts_at == now
    assert result.expires_at == now + timedelta(days=30)
    assert len(repo.created_subscriptions) == 1
    assert len(repo.created_periods) == 1
    assert repo.created_periods[0]["is_paid"] is True


@pytest.mark.asyncio
async def test_apply_paid_period_extends_existing_subscription_with_same_tariff() -> None:
    service = SubscriptionService()
    now = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)
    current_expires_at = now + timedelta(days=5)
    repo = FakeSubscriptionRepo(
        active_subscription={"subscription_id": 77, "tariff_id": 3},
        last_period={"expires_at": current_expires_at},
    )

    result = await service.apply_paid_period(
        subscription_repo=repo,
        user_id=10,
        tariff_id=3,
        period_days=30,
        now=now,
    )

    assert result.created_new_subscription is False
    assert result.subscription_id == 77
    assert result.starts_at == current_expires_at
    assert result.expires_at == current_expires_at + timedelta(days=30)
    assert repo.ended_subscriptions == []
    assert repo.created_subscriptions == []


@pytest.mark.asyncio
async def test_apply_paid_period_switches_subscription_when_tariff_changes() -> None:
    service = SubscriptionService()
    now = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)
    repo = FakeSubscriptionRepo(active_subscription={"subscription_id": 55, "tariff_id": 1})

    result = await service.apply_paid_period(
        subscription_repo=repo,
        user_id=10,
        tariff_id=2,
        period_days=30,
        now=now,
    )

    assert result.created_new_subscription is True
    assert len(repo.ended_subscriptions) == 1
    assert repo.ended_subscriptions[0]["subscription_id"] == 55
    assert len(repo.created_subscriptions) == 1
    assert repo.created_subscriptions[0]["tariff_id"] == 2
