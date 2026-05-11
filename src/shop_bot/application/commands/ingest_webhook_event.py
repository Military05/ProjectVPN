from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shop_bot.application.commands._helpers import enqueue_job
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return None

        return int(value)

    return int(value)


async def ingest_webhook_event(
    container: ServiceContainer,
    *,
    provider: str,
    payload: dict[str, Any],
    headers: Mapping[str, str],
) -> Mapping[str, Any]:
    adapter = container.payment_registry.get(provider)
    normalized = adapter.normalize_webhook(payload=payload, headers=headers)
    now = utcnow()

    async with container.uow() as uow:
        inserted, inbox = await uow.payments.save_webhook_inbox(
            provider=provider,
            event_key=normalized.event_key,
            headers=dict(headers),
            payload=payload,
            status="received",
        )

        order_id = _to_int_or_none(normalized.payment_order_id)

        attempt = None
        if normalized.provider_payment_id:
            attempt = await uow.payments.get_attempt_by_provider_payment_id(
                provider,
                normalized.provider_payment_id,
            )

            if attempt is not None and order_id is None:
                order_id = _to_int_or_none(attempt["payment_order_id"])

        payment_attempt_id = (
            _to_int_or_none(attempt["payment_attempt_id"])
            if attempt is not None
            else None
        )

        payment_event = await uow.payments.create_payment_event(
            payment_order_id=order_id,
            payment_attempt_id=payment_attempt_id,
            provider=provider,
            event_type=normalized.event_type,
            event_key=normalized.event_key,
            status="received",
            payload=normalized.payload,
            occurred_at=normalized.occurred_at,
        )

        if order_id is not None:
            await uow.payments.create_provider_transaction(
                payment_order_id=order_id,
                payment_attempt_id=payment_attempt_id,
                provider=provider,
                transaction_type=normalized.event_type,
                provider_transaction_key=normalized.event_key,
                transaction_status=normalized.status,
                amount_minor=int(normalized.amount_minor or 0),
                currency=str(
                    normalized.currency
                    or payload.get("currency")
                    or container.settings.default_currency
                ),
                payload=normalized.payload,
            )

        await uow.payments.mark_webhook_inbox_status(
            int(inbox["webhook_inbox_id"]),
            "processed",
            processed_at=now,
        )

    await enqueue_job(
        container,
        "process_payment_event",
        int(payment_event["payment_event_id"]),
    )

    return {
        "duplicate": not inserted,
        "event_key": normalized.event_key,
        "payment_event_id": int(payment_event["payment_event_id"]),
    }