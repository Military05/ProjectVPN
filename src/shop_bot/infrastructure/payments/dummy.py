from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from shop_bot.core.config import Settings
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent
from shop_bot.infrastructure.payments.base import PaymentAdapter


class DummyPaymentAdapter(PaymentAdapter):
    provider = "dummy"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_payment(self, *, order: Mapping[str, Any], return_url: str) -> PaymentIntent:
        provider_payment_id = f"dummy-{order['payment_order_id']}-{uuid4().hex[:8]}"
        payment_url = (
            f"{str(self.settings.dummy_payment_base_url).rstrip('/')}/sandbox/payments/"
            f"{order['payment_order_id']}?provider_payment_id={provider_payment_id}"
        )
        return PaymentIntent(
            provider=self.provider,
            provider_payment_id=provider_payment_id,
            status="pending",
            payment_url=payment_url,
            payload={"return_url": return_url},
        )

    def normalize_webhook(
        self,
        *,
        payload: dict[str, Any],
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        event_key = str(payload.get("event_key") or f"dummy:{payload['payment_order_id']}:{payload.get('status', 'paid')}")
        status = payload.get("status", "paid")
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=event_key,
            event_type=payload.get("event_type", f"dummy.{status}"),
            status=status,
            occurred_at=datetime.now(UTC),
            provider_payment_id=payload.get("provider_payment_id"),
            payment_order_id=int(payload["payment_order_id"]),
            amount_minor=payload.get("amount_minor"),
            currency=payload.get("currency"),
            payload=payload,
        )
