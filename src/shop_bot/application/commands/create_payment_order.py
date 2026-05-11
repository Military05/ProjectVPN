from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from shop_bot.application.commands._helpers import enqueue_job
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.exceptions import NotFoundError, ValidationError


async def create_payment_order(
    container: ServiceContainer,
    *,
    telegram_id: int,
    tariff_id: int,
    provider: str | None = None,
    idempotency_key: str | None = None,
    username: str | None = None,
    name_or_nick: str | None = None,
) -> Mapping[str, Any]:
    provider_name = provider or container.settings.payment_default_provider
    order_idempotency_key = idempotency_key or uuid4().hex

    async with container.uow() as uow:
        user = await uow.users.get_user_by_contact("telegram_id", str(telegram_id))
        if user is None:
            user = await uow.users.register_telegram_user(
                telegram_id=telegram_id,
                username=username,
                name_or_nick=name_or_nick or username or f"telegram-{telegram_id}",
            )

        tariff = await uow.admin.get_tariff(tariff_id)
        if tariff is None:
            raise NotFoundError("Tariff was not found")
        if not tariff["is_enabled"]:
            raise ValidationError("Tariff is disabled")

        order = await uow.payments.create_order(
            user_id=int(user["user_id"]),
            tariff_id=tariff_id,
            provider=provider_name,
            status="pending",
            amount_minor=int(tariff["price_minor"]),
            currency=str(tariff["currency"]),
            requested_period_days=int(tariff["period_days"]),
            idempotency_key=order_idempotency_key,
            metadata={"telegram_id": telegram_id},
        )
        existing_attempt = await uow.payments.get_latest_attempt(int(order["payment_order_id"]))

    if existing_attempt is not None:
        return {
            "payment_order_id": int(order["payment_order_id"]),
            "provider": str(order["provider"]),
            "status": str(order["status"]),
            "amount_minor": int(order["amount_minor"]),
            "currency": str(order["currency"]),
            "payment_url": existing_attempt.get("payment_url"),
            "provider_payment_id": existing_attempt.get("provider_payment_id"),
            "tariff_name": tariff["tariff_name"],
        }

    adapter = container.payment_registry.get(provider_name)
    intent = await adapter.create_payment(order=order, return_url=str(container.settings.payment_return_url))

    async with container.uow() as uow:
        current_attempt = await uow.payments.get_latest_attempt(int(order["payment_order_id"]))
        if current_attempt is None:
            attempt = await uow.payments.create_attempt(
                payment_order_id=int(order["payment_order_id"]),
                provider=provider_name,
                provider_payment_id=intent.provider_payment_id,
                status=intent.status,
                payment_url=intent.payment_url,
                payload=intent.payload,
            )
            await uow.payments.create_provider_transaction(
                payment_order_id=int(order["payment_order_id"]),
                payment_attempt_id=int(attempt["payment_attempt_id"]),
                provider=provider_name,
                transaction_type="invoice_created",
                provider_transaction_key=intent.provider_payment_id,
                transaction_status=intent.status,
                amount_minor=int(order["amount_minor"]),
                currency=str(order["currency"]),
                payload=intent.payload,
            )
        else:
            attempt = current_attempt

    await enqueue_job(container, "publish_outbox")
    return {
        "payment_order_id": int(order["payment_order_id"]),
        "provider": provider_name,
        "status": str(order["status"]),
        "amount_minor": int(order["amount_minor"]),
        "currency": str(order["currency"]),
        "payment_url": intent.payment_url,
        "provider_payment_id": intent.provider_payment_id,
        "tariff_name": tariff["tariff_name"],
    }
