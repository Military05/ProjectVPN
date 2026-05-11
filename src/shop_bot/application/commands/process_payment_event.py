from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shop_bot.application.commands._helpers import enqueue_job
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow


async def process_payment_event(container: ServiceContainer, *, payment_event_id: int) -> Mapping[str, Any]:
    now = utcnow()
    provision_subscription_id: int | None = None

    async with container.uow() as uow:
        event = await uow.payments.get_payment_event(payment_event_id, for_update=True)
        if event is None:
            return {"status": "missing"}
        if event["status"] in {"processed", "ignored"}:
            return {"status": str(event["status"])}

        order_id = event["payment_order_id"]
        attempt = None
        if event["payment_attempt_id"] is not None:
            attempt = await uow.payments.get_attempt(int(event["payment_attempt_id"]))
            if order_id is None and attempt is not None:
                order_id = int(attempt["payment_order_id"])
        if order_id is None:
            await uow.payments.mark_payment_event_status(payment_event_id, "ignored", processed_at=now)
            return {"status": "ignored"}

        order = await uow.payments.get_order(int(order_id), for_update=True)
        if order is None:
            await uow.payments.mark_payment_event_status(payment_event_id, "ignored", processed_at=now)
            return {"status": "ignored"}

        if attempt is None:
            attempt = await uow.payments.get_latest_attempt(int(order_id))

        normalized_status = _infer_status(event)
        if normalized_status == "paid":
            if attempt is not None:
                await uow.payments.update_attempt_status(
                    int(attempt["payment_attempt_id"]),
                    "paid",
                    provider_payment_id=event["payload"].get("provider_payment_id") if isinstance(event["payload"], dict) else None,
                    payload=event["payload"],
                )
            if order["status"] != "paid":
                await uow.payments.mark_order_paid(int(order_id), now)
                activation = await container.subscription_service.apply_paid_period(
                    subscription_repo=uow.subscriptions,
                    user_id=int(order["user_id"]),
                    tariff_id=int(order["tariff_id"]),
                    period_days=int(order["requested_period_days"]),
                    now=now,
                )
                provision_subscription_id = activation.subscription_id
                await uow.payments.create_outbox_event(
                    event_name="subscription_activated",
                    aggregate_type="subscription",
                    aggregate_id=activation.subscription_id,
                    payload={
                        "subscription_id": activation.subscription_id,
                        "subscription_period_id": activation.subscription_period_id,
                        "starts_at": activation.starts_at.isoformat(),
                        "expires_at": activation.expires_at.isoformat(),
                    },
                )
            await uow.payments.create_provider_transaction(
                payment_order_id=int(order_id),
                payment_attempt_id=int(attempt["payment_attempt_id"]) if attempt is not None else None,
                provider=str(event["provider"]),
                transaction_type="payment_paid",
                provider_transaction_key=str(event["event_key"]),
                transaction_status="paid",
                amount_minor=int(order["amount_minor"]),
                currency=str(order["currency"]),
                payload=event["payload"],
            )
            await uow.payments.mark_payment_event_status(payment_event_id, "processed", processed_at=now)
        elif normalized_status in {"failed", "expired", "cancelled"}:
            if attempt is not None:
                await uow.payments.update_attempt_status(
                    int(attempt["payment_attempt_id"]),
                    normalized_status,
                    payload=event["payload"],
                )
            if order["status"] == "pending":
                await uow.payments.mark_order_status(int(order_id), normalized_status, now)
            await uow.payments.create_outbox_event(
                event_name=f"payment_{normalized_status}",
                aggregate_type="payment_order",
                aggregate_id=int(order_id),
                payload={"payment_order_id": int(order_id), "status": normalized_status},
            )
            await uow.payments.mark_payment_event_status(payment_event_id, "processed", processed_at=now)
        else:
            await uow.payments.mark_payment_event_status(payment_event_id, "ignored", processed_at=now)

    if provision_subscription_id is not None:
        await enqueue_job(container, "provision_subscription", provision_subscription_id)
    await enqueue_job(container, "publish_outbox")
    return {"status": "processed", "subscription_id": provision_subscription_id}


def _infer_status(event: Mapping[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        raw_status = payload.get("status") or payload.get("payment_status") or payload.get("state")
        if isinstance(raw_status, str):
            lowered = raw_status.lower()
            if lowered in {"paid", "succeeded", "success", "completed"}:
                return "paid"
            if lowered in {"failed", "error"}:
                return "failed"
            if lowered in {"expired"}:
                return "expired"
            if lowered in {"cancelled", "canceled"}:
                return "cancelled"
    event_type = str(event["event_type"]).lower()
    if "paid" in event_type or "succeeded" in event_type or "completed" in event_type:
        return "paid"
    if "cancel" in event_type:
        return "cancelled"
    if "expire" in event_type:
        return "expired"
    if "fail" in event_type or "error" in event_type:
        return "failed"
    return "pending"
