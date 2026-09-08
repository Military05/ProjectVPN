from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import UUID, uuid4

from shop_bot.application.job_names import JobName
from shop_bot.application.ports import JobQueue, PaymentGatewayRegistry, UnitOfWorkFactory
from shop_bot.core.exceptions import ConflictError, NotFoundError, ValidationError
from shop_bot.domain.entities.payment import PaymentAttempt, PaymentOrder, PaymentStatus
from shop_bot.domain.payments.models import PaymentIntent


@dataclass(frozen=True, slots=True)
class PreparedPaymentOrder:
    order: PaymentOrder
    tariff_name: str
    attempt: PaymentAttempt
    owns_creation_claim: bool
    creation_lease_token: UUID | None = None


@dataclass(slots=True)
class CreatePayment:
    uow_factory: UnitOfWorkFactory
    payment_registry: PaymentGatewayRegistry
    job_queue: JobQueue
    default_provider: str
    return_url: str
    creation_lease_seconds: int = 30
    clock: Callable[[], datetime] | None = None

    async def execute(
        self,
        *,
        telegram_id: int,
        tariff_id: int,
        provider: str | None = None,
        idempotency_key: str | None = None,
        username: str | None = None,
        name_or_nick: str | None = None,
    ) -> dict[str, Any]:
        provider_name = provider or self.default_provider
        prepared = await self._prepare_order(
            telegram_id=telegram_id,
            tariff_id=tariff_id,
            provider=provider_name,
            idempotency_key=idempotency_key or uuid4().hex,
            username=username,
            name_or_nick=name_or_nick,
        )
        if not prepared.owns_creation_claim:
            return self._response(prepared.order, prepared.tariff_name, prepared.attempt)

        token = prepared.creation_lease_token
        if token is None:
            raise RuntimeError("Owned payment creation claim has no lease token")

        gateway = self.payment_registry.get(prepared.order.provider)
        try:
            intent = await gateway.create_payment(
                order=self._gateway_order(prepared.order),
                return_url=self.return_url,
            )
        except BaseException as exc:
            try:
                await self._fail_owned_claim(prepared.attempt.id, token)
            except Exception as cleanup_exc:  # preserve the original provider/application failure
                exc.add_note(f"Failed to persist payment creation failure: {type(cleanup_exc).__name__}")
            raise

        attempt = await self._finalize_owned_claim(
            order=prepared.order,
            payment_attempt_id=prepared.attempt.id,
            lease_token=token,
            intent=intent,
        )
        await self.job_queue.enqueue(JobName.PUBLISH_OUTBOX)
        return self._response(prepared.order, prepared.tariff_name, attempt)

    async def _prepare_order(
        self,
        *,
        telegram_id: int,
        tariff_id: int,
        provider: str,
        idempotency_key: str,
        username: str | None,
        name_or_nick: str | None,
    ) -> PreparedPaymentOrder:
        async with self.uow_factory() as uow:
            user = await uow.users.get_entity_by_contact("telegram_id", str(telegram_id))
            if user is None:
                user = await uow.users.register_telegram_entity(
                    telegram_id,
                    username,
                    name_or_nick or username or f"telegram-{telegram_id}",
                )
            tariff = await uow.admin.get_tariff_entity(tariff_id)
            if tariff is None:
                raise NotFoundError("Tariff was not found")
            if not tariff.is_enabled:
                raise ValidationError("Tariff is disabled")
            if user.id is None or tariff.id is None:
                raise RuntimeError("Persisted user and tariff must have identifiers")

            candidate = PaymentOrder(
                id=None,
                user_id=user.id,
                tariff_id=tariff.id,
                provider=provider,
                status=PaymentStatus.PENDING,
                amount=tariff.price,
                requested_period_days=tariff.period_days,
                idempotency_key=idempotency_key,
                metadata={"telegram_id": telegram_id},
            )
            resolved = await uow.payments.add_order_entity(candidate)
            if resolved.id is None:
                raise RuntimeError("Payment order was not persisted")
            order = await uow.payments.get_order_entity(resolved.id, for_update=True)
            if order is None:
                raise RuntimeError("Payment order disappeared during creation")
            if not order.has_same_creation_identity(candidate):
                raise ConflictError("Idempotency key is already used by another payment request")

            now = self._now()
            attempt = await uow.payments.get_latest_attempt_entity(order.id)
            if attempt is not None:
                if attempt.status is PaymentStatus.CREATED:
                    if attempt.creation_claim_is_active(now):
                        return PreparedPaymentOrder(order, tariff.name, attempt, False)
                    attempt.fail_creation(now=now, diagnostic="creation_claim_abandoned")
                    await uow.payments.save_attempt_entity(attempt)
                elif not (
                    attempt.status is PaymentStatus.FAILED
                    and attempt.provider_payment_id is None
                ):
                    return PreparedPaymentOrder(order, tariff.name, attempt, False)

            token = uuid4()
            claim = PaymentAttempt(
                id=None,
                payment_order_id=order.id,
                provider=order.provider,
                status=PaymentStatus.CREATED,
                payload={"creation_state": "claimed"},
            )
            claim.claim_creation(
                now=now,
                lease_expires_at=now + timedelta(seconds=self.creation_lease_seconds),
                lease_token=token,
            )
            claim = await uow.payments.add_attempt_entity(claim)
            return PreparedPaymentOrder(order, tariff.name, claim, True, token)

    async def _finalize_owned_claim(
        self,
        *,
        order: PaymentOrder,
        payment_attempt_id: int | None,
        lease_token: UUID,
        intent: PaymentIntent,
    ) -> PaymentAttempt:
        if payment_attempt_id is None or order.id is None:
            raise RuntimeError("Payment creation claim must be persisted before finalization")
        async with self.uow_factory() as uow:
            attempt = await uow.payments.get_attempt_entity(payment_attempt_id, for_update=True)
            if attempt is None:
                raise RuntimeError("Payment creation claim disappeared before finalization")
            if not attempt.owns_creation_claim(lease_token):
                current = await uow.payments.get_latest_attempt_entity(order.id)
                if current is None:
                    raise ConflictError("Payment creation ownership was lost")
                return current

            now = self._now()
            attempt.finalize_creation(
                status=PaymentStatus(intent.status),
                provider_payment_id=intent.provider_payment_id,
                payment_url=intent.payment_url,
                payload=intent.payload,
                now=now,
            )
            await uow.payments.save_attempt_entity(attempt)
            await uow.payments.create_provider_transaction(
                payment_order_id=order.id,
                payment_attempt_id=attempt.id,
                provider=order.provider,
                transaction_type="invoice_created",
                provider_transaction_key=intent.provider_payment_id,
                transaction_status=str(intent.status),
                amount_minor=order.amount.minor,
                currency=order.amount.currency,
                payload=intent.payload,
            )
            return attempt

    async def _fail_owned_claim(self, payment_attempt_id: int | None, lease_token: UUID) -> None:
        if payment_attempt_id is None:
            return
        async with self.uow_factory() as uow:
            attempt = await uow.payments.get_attempt_entity(payment_attempt_id, for_update=True)
            if attempt is None or not attempt.owns_creation_claim(lease_token):
                return
            attempt.fail_creation(now=self._now(), diagnostic="provider_create_failed")
            await uow.payments.save_attempt_entity(attempt)

    def _now(self) -> datetime:
        if self.clock is None:
            from shop_bot.core.time import utcnow

            return utcnow()
        return self.clock()

    @staticmethod
    def _gateway_order(order: PaymentOrder) -> dict[str, Any]:
        return {
            "payment_order_id": order.id,
            "user_id": order.user_id,
            "tariff_id": order.tariff_id,
            "provider": order.provider,
            "status": str(order.status),
            "amount_minor": order.amount.minor,
            "currency": order.amount.currency,
            "requested_period_days": order.requested_period_days,
            "idempotency_key": order.idempotency_key,
            "metadata": order.metadata,
        }

    @staticmethod
    def _response(order: PaymentOrder, tariff_name: str, attempt: PaymentAttempt) -> dict[str, Any]:
        return {
            "payment_order_id": order.id,
            "provider": order.provider,
            "status": str(order.status),
            "amount_minor": order.amount.minor,
            "currency": order.amount.currency,
            "payment_url": attempt.payment_url,
            "provider_payment_id": attempt.provider_payment_id,
            "tariff_name": tariff_name,
        }
