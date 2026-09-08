from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from shop_bot.core.config import Settings
from shop_bot.core.exceptions import WebhookAuthenticationError, WebhookPayloadError
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent
from shop_bot.infrastructure.payments.base import PaymentAdapter


class DummyPaymentAdapter(PaymentAdapter):
    provider = "dummy"

    def __init__(self, settings: Settings) -> None:
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

    async def verify_and_normalize_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        del headers
        if self.settings.is_production:
            raise WebhookAuthenticationError("Dummy payment webhooks are disabled in production")
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookPayloadError("Dummy webhook payload is invalid") from exc
        if not isinstance(payload, dict):
            raise WebhookPayloadError("Dummy webhook payload is invalid")
        try:
            payment_order_id = int(payload["payment_order_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WebhookPayloadError("Dummy payment order id is invalid") from exc
        status = str(payload.get("status", "paid")).lower()
        provider_payment_id = payload.get("provider_payment_id")
        event_key = str(payload.get("event_key") or f"dummy:{payment_order_id}:{status}")
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=event_key,
            event_type=str(payload.get("event_type", f"dummy.{status}")),
            status="paid" if status == "paid" else "pending",
            occurred_at=datetime.now(UTC),
            provider_payment_id=str(provider_payment_id) if provider_payment_id not in (None, "") else None,
            payment_order_id=payment_order_id,
            amount_minor=self._optional_int(payload.get("amount_minor")),
            currency=self._optional_currency(payload.get("currency")),
            payload=payload,
        )

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise WebhookPayloadError("Dummy amount is invalid") from exc

    @staticmethod
    def _optional_currency(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        return normalized or None
