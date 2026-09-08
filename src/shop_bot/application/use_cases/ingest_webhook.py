from __future__ import annotations

from shop_bot.application.job_names import JobName
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from shop_bot.application.ports import JobQueue, PaymentGatewayRegistry, UnitOfWorkFactory
from shop_bot.core.exceptions import WebhookPayloadError


@dataclass(slots=True)
class IngestWebhook:
    uow_factory: UnitOfWorkFactory
    payment_registry: PaymentGatewayRegistry
    job_queue: JobQueue
    default_currency: str
    clock: Callable[[], datetime]

    async def execute(
        self,
        *,
        provider: str,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        gateway = self.payment_registry.get(provider)

        # Security boundary: no UoW, persistence, or queueing before provider verification.
        normalized = await gateway.verify_and_normalize_webhook(
            raw_body=raw_body,
            headers=headers,
        )
        raw_payload = self._parse_raw_payload(raw_body)
        now = self.clock()

        async with self.uow_factory() as uow:
            inserted, inbox = await uow.payments.save_webhook_inbox(
                provider=provider,
                event_key=normalized.event_key,
                headers=dict(headers),
                payload=raw_payload,
                status="received",
            )

            attempt = None
            if normalized.provider_payment_id:
                attempt = await uow.payments.get_attempt_entity_by_provider_payment_id(
                    provider,
                    normalized.provider_payment_id,
                )
            authoritative_order_id = attempt.payment_order_id if attempt is not None else None

            payment_event = await uow.payments.create_payment_event(
                payment_order_id=authoritative_order_id,
                payment_attempt_id=attempt.id if attempt is not None else None,
                provider=provider,
                event_type=normalized.event_type,
                event_key=normalized.event_key,
                status="received",
                payload=normalized.payload,
                occurred_at=normalized.occurred_at,
                provider_payment_id=normalized.provider_payment_id,
                reported_payment_order_id=normalized.payment_order_id,
                payment_status=str(normalized.status) if normalized.status is not None else None,
                amount_minor=normalized.amount_minor,
                currency=self._canonical_currency(normalized.currency),
            )
            await uow.payments.mark_webhook_inbox_status(
                int(inbox["webhook_inbox_id"]),
                "processed",
                processed_at=now,
            )

        event_id = int(payment_event["payment_event_id"])
        await self.job_queue.enqueue(JobName.PROCESS_PAYMENT_EVENT, event_id)
        return {
            "duplicate": not inserted,
            "event_key": normalized.event_key,
            "payment_event_id": event_id,
        }

    @staticmethod
    def _parse_raw_payload(raw_body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookPayloadError("Webhook payload is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise WebhookPayloadError("Webhook payload must be a JSON object")
        return payload

    @staticmethod
    def _canonical_currency(currency: str | None) -> str | None:
        if currency is None:
            return None
        normalized = currency.strip().upper()
        return normalized or None
