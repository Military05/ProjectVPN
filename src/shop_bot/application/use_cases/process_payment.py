from __future__ import annotations

from shop_bot.application.job_names import JobName
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from shop_bot.application.ports import JobQueue, UnitOfWorkFactory
from shop_bot.application.use_cases.activate_subscription import ActivateSubscription
from shop_bot.domain.entities.payment import (
    PaymentAttempt,
    PaymentEvent,
    PaymentEventStatus,
    PaymentOrder,
    PaymentStatus,
    PaymentTransitionResult,
)
from shop_bot.domain.errors import PaymentInvariantViolation
from shop_bot.domain.repositories.interfaces import UnitOfWork
from shop_bot.domain.services.payment_settlement import validate_paid_settlement
from shop_bot.domain.services.subscription_policy import SubscriptionActivation


@dataclass(frozen=True, slots=True)
class PaymentProcessingContext:
    event: PaymentEvent
    order: PaymentOrder
    attempt: PaymentAttempt | None


@dataclass(slots=True)
class ProcessPayment:
    uow_factory: UnitOfWorkFactory
    activate_subscription: ActivateSubscription
    job_queue: JobQueue
    clock: Callable[[], datetime]

    async def execute(self, *, payment_event_id: int) -> dict[str, Any]:
        now = self.clock()
        provision_subscription_id: int | None = None
        revoke_vpn_configuration_id: int | None = None
        publish_outbox = True
        async with self.uow_factory() as uow:
            event = await uow.payments.get_event_entity(payment_event_id, for_update=True)
            if event is None:
                return {"status": "missing"}
            if event.status in {PaymentEventStatus.PROCESSED, PaymentEventStatus.IGNORED}:
                return {"status": str(event.status)}

            if event.event_type == "refund.succeeded":
                refund_result = await self._process_refund(uow, event=event, now=now)
                revoke_vpn_configuration_id = refund_result.pop("revoke_vpn_configuration_id", None)
                result = refund_result
            else:
                resolved = await self._resolve_context(uow, payment_event_id=payment_event_id, now=now)
                if isinstance(resolved, dict):
                    return resolved
                status = resolved.event.normalized_payment_status()
                if status is PaymentStatus.PAID:
                    paid_result = await self._process_paid(uow, context=resolved, now=now)
                    if isinstance(paid_result, dict):
                        publish_outbox = False
                        result = paid_result
                    else:
                        provision_subscription_id = paid_result
                        result = {"status": "processed", "subscription_id": provision_subscription_id}
                elif status in {PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELLED}:
                    await self._process_unsuccessful(uow, context=resolved, status=status, now=now)
                    result = {"status": "processed", "subscription_id": None}
                else:
                    resolved.event.ignore(now)
                    await uow.payments.save_event_entity(resolved.event)
                    result = {"status": "processed", "subscription_id": None}

        if provision_subscription_id is not None:
            await self.job_queue.enqueue(JobName.PROVISION_SUBSCRIPTION, provision_subscription_id)
        if revoke_vpn_configuration_id is not None:
            await self.job_queue.enqueue(JobName.REVOKE_VPN_CONFIGURATION, revoke_vpn_configuration_id, "expiration")
        if publish_outbox:
            await self.job_queue.enqueue(JobName.PUBLISH_OUTBOX)
        return result

    async def _process_refund(
        self, uow: UnitOfWork, *, event: PaymentEvent, now: datetime
    ) -> dict[str, Any]:
        if event.payment_attempt_id is None:
            return await self._reject_paid_event(uow, event, now, "refund has no original payment attempt")
        attempt = await uow.payments.get_attempt_entity(event.payment_attempt_id)
        if attempt is None:
            return await self._reject_paid_event(uow, event, now, "refund original payment attempt is missing")
        order = await uow.payments.get_order_entity(attempt.payment_order_id, for_update=True)
        if order is None:
            return await self._reject_paid_event(uow, event, now, "refund payment order is missing")
        attempt = await uow.payments.get_attempt_entity(event.payment_attempt_id, for_update=True)
        if attempt is None:
            return await self._reject_paid_event(uow, event, now, "refund original payment attempt is missing")
        if event.amount_minor is None or event.amount_minor <= 0 or event.currency is None:
            return await self._reject_paid_event(uow, event, now, "refund amount/currency is missing")
        if event.currency.strip().upper() != order.amount.currency or event.amount_minor > order.amount.minor:
            return await self._reject_paid_event(uow, event, now, "refund amount/currency does not match order")

        await uow.payments.create_provider_transaction(
            payment_order_id=int(order.id),
            payment_attempt_id=attempt.id,
            provider=event.provider,
            transaction_type="refund_succeeded",
            provider_transaction_key=event.event_key,
            transaction_status="succeeded",
            amount_minor=event.amount_minor,
            currency=event.currency.strip().upper(),
            payload=event.payload,
        )
        cumulative = await uow.payments.sum_provider_transactions(
            payment_order_id=int(order.id), provider=event.provider,
            transaction_type="refund_succeeded", transaction_status="succeeded"
        )
        revoke_id: int | None = None
        if cumulative >= order.amount.minor:
            period = await uow.subscriptions.get_period_by_payment_order_id(int(order.id), for_update=True)
            if period is None:
                await uow.payments.create_outbox_event(
                    event_name="refund_entitlement_reconciliation_required",
                    aggregate_type="payment_order",
                    aggregate_id=int(order.id),
                    payload={"payment_order_id": int(order.id), "refund_event_key": event.event_key},
                )
            else:
                period.is_paid = False
                await uow.subscriptions.save_period_entity(period)
                subscription = await uow.subscriptions.get_entity(int(period.subscription_id), for_update=True)
                current = await uow.subscriptions.get_current_period_entity(int(period.subscription_id), now)
                if current is None:
                    active_vpn = await uow.vpn.get_active_entity_for_subscription(int(period.subscription_id))
                    if active_vpn is not None and active_vpn.id is not None:
                        revoke_id = int(active_vpn.id)
                if subscription is not None and not await uow.subscriptions.has_funded_period_after(int(period.subscription_id), now):
                    subscription.expire(now)
                    await uow.subscriptions.save_entity(subscription)
        event.mark_processed(now)
        await uow.payments.save_event_entity(event)
        return {"status": "processed", "subscription_id": None, "revoke_vpn_configuration_id": revoke_id}

    async def _resolve_context(
        self,
        uow: UnitOfWork,
        *,
        payment_event_id: int,
        now: datetime,
    ) -> PaymentProcessingContext | dict[str, str]:
        event = await uow.payments.get_event_entity(payment_event_id, for_update=True)
        if event is None:
            return {"status": "missing"}
        if event.status in {PaymentEventStatus.PROCESSED, PaymentEventStatus.IGNORED}:
            return {"status": str(event.status)}

        status = event.normalized_payment_status()
        if status is PaymentStatus.PAID:
            return await self._resolve_paid_context(uow, event=event, now=now)

        order_id, attempt = await self._resolve_order_reference(uow, event)
        if order_id is None:
            return await self._ignore_event(uow, event, now)
        order = await uow.payments.get_order_entity(order_id, for_update=True)
        if order is None:
            return await self._ignore_event(uow, event, now)
        if event.payment_attempt_id is not None:
            attempt = await uow.payments.get_attempt_entity(event.payment_attempt_id, for_update=True)
        if attempt is None:
            # Legacy compatibility for non-paid status updates only. PAID never uses this fallback.
            attempt = await uow.payments.get_latest_attempt_entity(order_id)
        return PaymentProcessingContext(event=event, order=order, attempt=attempt)

    async def _resolve_paid_context(
        self,
        uow: UnitOfWork,
        *,
        event: PaymentEvent,
        now: datetime,
    ) -> PaymentProcessingContext | dict[str, str]:
        if event.payment_attempt_id is None:
            return await self._reject_paid_event(uow, event, now, "paid event has no exact payment attempt")
        attempt = await uow.payments.get_attempt_entity(event.payment_attempt_id)
        if attempt is None:
            return await self._reject_paid_event(uow, event, now, "exact payment attempt is missing")
        order = await uow.payments.get_order_entity(attempt.payment_order_id, for_update=True)
        if order is None:
            return await self._reject_paid_event(uow, event, now, "payment order is missing")
        # Re-read after acquiring the canonical order lock to avoid stale state.
        attempt = await uow.payments.get_attempt_entity(event.payment_attempt_id, for_update=True)
        if attempt is None:
            return await self._reject_paid_event(uow, event, now, "exact payment attempt is missing")
        return PaymentProcessingContext(event=event, order=order, attempt=attempt)

    @staticmethod
    async def _resolve_order_reference(uow: UnitOfWork, event: PaymentEvent) -> tuple[int | None, PaymentAttempt | None]:
        order_id = event.payment_order_id
        attempt = None
        if event.payment_attempt_id is not None:
            attempt = await uow.payments.get_attempt_entity(event.payment_attempt_id)
            if order_id is None and attempt is not None:
                order_id = attempt.payment_order_id
        return order_id, attempt

    @staticmethod
    async def _ignore_event(uow: UnitOfWork, event: PaymentEvent, now: datetime) -> dict[str, str]:
        event.ignore(now)
        await uow.payments.save_event_entity(event)
        return {"status": "ignored"}

    @staticmethod
    async def _reject_paid_event(
        uow: UnitOfWork,
        event: PaymentEvent,
        now: datetime,
        reason: str,
    ) -> dict[str, str]:
        event.fail(now, reason)
        await uow.payments.save_event_entity(event)
        return {"status": "rejected"}

    async def _process_paid(
        self,
        uow: UnitOfWork,
        *,
        context: PaymentProcessingContext,
        now: datetime,
    ) -> int | None | dict[str, str]:
        event, order, attempt = context.event, context.order, context.attempt
        try:
            validate_paid_settlement(event=event, attempt=attempt, order=order)
        except PaymentInvariantViolation as exc:
            return await self._reject_paid_event(uow, event, now, f"payment settlement rejected: {exc}")

        assert attempt is not None
        assert event.amount_minor is not None
        assert event.currency is not None

        transition = attempt.apply_provider_status(PaymentStatus.PAID, payload=event.payload)
        if transition is PaymentTransitionResult.STALE:
            event.error_message = f"stale paid event for attempt already {attempt.status}"
            event.ignore(now, event.error_message)
            await uow.payments.save_event_entity(event)
            return {"status": "ignored"}
        await uow.payments.save_attempt_entity(attempt)

        subscription_id = None
        if order.mark_paid(now):
            await uow.payments.save_order_entity(order)
            activation = await self.activate_subscription.execute(
                repository=uow.subscriptions,
                user_id=order.user_id,
                tariff_id=order.tariff_id,
                period_days=order.requested_period_days,
                now=now,
                payment_order_id=int(order.id),
            )
            if activation.subscription.id is None or activation.period.id is None:
                raise RuntimeError("Subscription activation was not persisted")
            subscription_id = activation.subscription.id
            await self._emit_subscription_activated(uow, activation)

        await uow.payments.create_provider_transaction(
            payment_order_id=int(order.id),
            payment_attempt_id=attempt.id,
            provider=event.provider,
            transaction_type="payment_paid",
            provider_transaction_key=event.event_key,
            transaction_status="paid",
            amount_minor=event.amount_minor,
            currency=event.currency.strip().upper(),
            payload=event.payload,
        )
        event.mark_processed(now)
        await uow.payments.save_event_entity(event)
        return subscription_id

    @staticmethod
    async def _emit_subscription_activated(
        uow: UnitOfWork,
        activation: SubscriptionActivation,
    ) -> None:
        await uow.payments.create_outbox_event(
            event_name="subscription_activated",
            aggregate_type="subscription",
            aggregate_id=activation.subscription.id,
            payload={
                "subscription_id": activation.subscription.id,
                "subscription_period_id": activation.period.id,
                "starts_at": activation.period.starts_at.isoformat(),
                "expires_at": activation.period.expires_at.isoformat(),
            },
        )

    @staticmethod
    async def _process_unsuccessful(
        uow: UnitOfWork,
        *,
        context: PaymentProcessingContext,
        status: PaymentStatus,
        now: datetime,
    ) -> None:
        event, order, attempt = context.event, context.order, context.attempt
        if attempt is not None:
            transition = attempt.apply_provider_status(status, payload=event.payload)
            if transition is PaymentTransitionResult.STALE:
                event.error_message = f"stale provider status {status} for attempt already {attempt.status}"
                event.ignore(now, event.error_message)
                await uow.payments.save_event_entity(event)
                return
            await uow.payments.save_attempt_entity(attempt)
        if order.apply_external_status(status, now):
            await uow.payments.save_order_entity(order)
        await uow.payments.create_outbox_event(
            event_name=f"payment_{status}",
            aggregate_type="payment_order",
            aggregate_id=int(order.id),
            payload={"payment_order_id": order.id, "status": str(status)},
        )
        event.mark_processed(now)
        await uow.payments.save_event_entity(event)
