from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from shop_bot.domain.entities.node import NodeTask, NodeTaskOperation, NodeTaskStatus
from shop_bot.domain.entities.payment import PaymentEvent, PaymentEventStatus, PaymentOrder, PaymentStatus
from shop_bot.domain.entities.subscription import Subscription, SubscriptionPeriod, SubscriptionStatus
from shop_bot.domain.entities.vpn import VpnConfiguration, VpnConfigurationStatus
from shop_bot.domain.errors import InvalidStateTransition
from shop_bot.domain.services.subscription_policy import SubscriptionPolicy
from shop_bot.domain.value_objects.money import Money


def test_payment_order_enforces_state_transitions() -> None:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    order = PaymentOrder(
        id=1,
        user_id=2,
        tariff_id=3,
        provider="dummy",
        status=PaymentStatus.PENDING,
        amount=Money(9900, "rub"),
        requested_period_days=30,
        idempotency_key="order-1",
    )

    assert order.mark_paid(now) is True
    assert order.status is PaymentStatus.PAID
    assert order.paid_at == now
    with pytest.raises(InvalidStateTransition):
        order.cancel(now)


def test_payment_event_normalizes_provider_status() -> None:
    event = PaymentEvent(
        id=1,
        payment_order_id=2,
        payment_attempt_id=3,
        provider="provider",
        event_type="invoice.updated",
        event_key="evt-1",
        status=PaymentEventStatus.RECEIVED,
        payload={"state": "completed"},
        occurred_at=datetime(2026, 8, 6, tzinfo=UTC),
    )

    assert event.normalized_payment_status() is PaymentStatus.PAID


def test_subscription_policy_extends_existing_period() -> None:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    subscription = Subscription(
        id=10,
        user_id=2,
        tariff_id=3,
        status=SubscriptionStatus.ACTIVE,
        created_at=now - timedelta(days=20),
    )
    last_period = SubscriptionPeriod(
        id=20,
        subscription_id=10,
        starts_at=now - timedelta(days=20),
        expires_at=now + timedelta(days=5),
        is_paid=True,
        created_at=now - timedelta(days=20),
    )

    result = SubscriptionPolicy().apply_paid_period(
        active_subscription=subscription,
        last_period=last_period,
        user_id=2,
        tariff_id=3,
        period_days=30,
        now=now,
    )

    assert result.created_new_subscription is False
    assert result.period.starts_at == last_period.expires_at
    assert result.period.expires_at == last_period.expires_at + timedelta(days=30)


def test_node_task_calculates_bounded_retry() -> None:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    task = NodeTask(
        id=1,
        task_uuid=uuid4(),
        node_id=2,
        operation=NodeTaskOperation.PROVISION_CLIENT,
        status=NodeTaskStatus.PENDING,
        idempotency_key="provision:1",
        attempts=0,
        max_attempts=3,
        next_retry_at=now,
    )

    lease_token = uuid4()
    assert task.start_attempt(
        now,
        lease_expires_at=now + timedelta(seconds=60),
        lease_token=lease_token,
    ) == 1
    task.retry(error="offline", response=None, now=now, base_delay_seconds=10)
    assert task.status is NodeTaskStatus.PENDING
    assert task.next_retry_at == now + timedelta(seconds=10)


def test_vpn_configuration_lifecycle() -> None:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    configuration = VpnConfiguration(
        id=1,
        subscription_id=2,
        server_endpoint_id=3,
        client_uuid=uuid4(),
        display_name="vpn-2",
        status=VpnConfigurationStatus.PROVISIONING,
    )

    configuration.activate("remote-1")
    configuration.begin_revoke()
    configuration.revoke(now)

    assert configuration.status is VpnConfigurationStatus.REVOKED
    assert configuration.remote_client_ref == "remote-1"
    assert configuration.revoked_at == now


def _vpn(status: VpnConfigurationStatus = VpnConfigurationStatus.PROVISIONING) -> VpnConfiguration:
    return VpnConfiguration(
        id=1,
        subscription_id=2,
        server_endpoint_id=3,
        client_uuid=uuid4(),
        display_name="vpn-2",
        status=status,
    )


def test_vpn_configuration_strict_normal_transitions() -> None:
    now = datetime(2026, 8, 19, tzinfo=UTC)

    provisioned = _vpn()
    provisioned.activate("remote")
    assert provisioned.status is VpnConfigurationStatus.ACTIVE
    assert provisioned.generation == 1

    failed = _vpn()
    failed.fail_provisioning()
    assert failed.status is VpnConfigurationStatus.FAILED
    assert failed.generation == 1

    provisioned.begin_revoke()
    assert provisioned.status is VpnConfigurationStatus.REVOKING
    assert str(provisioned.desired_state) == "revoked"
    assert provisioned.generation == 2
    provisioned.revoke(now)
    assert provisioned.status is VpnConfigurationStatus.REVOKED

    revoke_failed = _vpn()
    revoke_failed.activate()
    revoke_failed.begin_revoke()
    revoke_failed.fail_revoke()
    assert revoke_failed.status is VpnConfigurationStatus.REVOKE_FAILED


def test_vpn_configuration_forbids_implicit_lifecycle_transitions() -> None:
    now = datetime(2026, 8, 19, tzinfo=UTC)

    revoking = _vpn()
    revoking.activate()
    revoking.begin_revoke()
    with pytest.raises(InvalidStateTransition):
        revoking.activate()

    revoked = _vpn()
    revoked.activate()
    revoked.begin_revoke()
    revoked.revoke(now)
    with pytest.raises(InvalidStateTransition):
        revoked.activate()

    failed = _vpn()
    failed.fail_provisioning()
    with pytest.raises(InvalidStateTransition):
        failed.activate()
    with pytest.raises(InvalidStateTransition):
        failed.begin_revoke()

    provisioning = _vpn()
    with pytest.raises(InvalidStateTransition):
        provisioning.revoke(now)

    active = _vpn()
    active.activate()
    with pytest.raises(InvalidStateTransition):
        active.fail_provisioning()
    with pytest.raises(InvalidStateTransition):
        active.revoke(now)


def test_vpn_deliberate_recovery_changes_generation_explicitly() -> None:
    config = _vpn()
    config.activate()
    config.begin_revoke()
    config.fail_revoke()
    assert config.generation == 2

    config.retry_revoke()
    assert config.status is VpnConfigurationStatus.REVOKING
    assert config.generation == 3
    assert str(config.desired_state) == "revoked"

    provisioning = _vpn()
    provisioning.cancel_provisioning_for_revoke()
    assert provisioning.status is VpnConfigurationStatus.FAILED
    assert str(provisioning.desired_state) == "revoked"
    assert provisioning.generation == 2
