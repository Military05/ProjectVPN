from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from shop_bot.core.config import Settings
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent
from shop_bot.infrastructure.payments.base import PaymentAdapter


class YooKassaAdapter(PaymentAdapter):
    provider = "yookassa"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_payment(self, *, order: Mapping[str, Any], return_url: str) -> PaymentIntent:
        if not self.settings.yookassa_shop_id or not self.settings.yookassa_secret_key:
            raise RuntimeError("YooKassa credentials are not configured")
        auth = b64encode(
            f"{self.settings.yookassa_shop_id}:{self.settings.yookassa_secret_key}".encode("utf-8")
        ).decode("utf-8")
        amount = Decimal(order["amount_minor"]) / Decimal("100")
        payload = {
            "amount": {"value": f"{amount:.2f}", "currency": order["currency"]},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": str(self.settings.yookassa_return_url)},
            "description": f"Subscription order {order['payment_order_id']}",
            "metadata": {"payment_order_id": int(order["payment_order_id"]), "provider": self.provider},
        }
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(
                "https://api.yookassa.ru/v3/payments",
                json=payload,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Idempotence-Key": str(order["idempotency_key"]),
                },
            )
            response.raise_for_status()
            body = response.json()
        return PaymentIntent(
            provider=self.provider,
            provider_payment_id=str(body["id"]),
            status=body.get("status", "pending"),
            payment_url=body.get("confirmation", {}).get("confirmation_url"),
            payload=body,
        )

    def normalize_webhook(
        self,
        *,
        payload: dict[str, Any],
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        obj = payload.get("object", {})
        event = str(payload.get("event", "payment.unknown"))
        status = str(obj.get("status", "pending"))
        event_key = f"yookassa:{obj.get('id', 'unknown')}:{event}:{status}"
        metadata = obj.get("metadata") or {}
        amount = obj.get("amount") or {}
        occurred_at = datetime.now(UTC)
        if obj.get("captured_at"):
            occurred_at = datetime.fromisoformat(obj["captured_at"].replace("Z", "+00:00"))
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=event_key,
            event_type=event,
            status="paid" if status == "succeeded" else status,
            occurred_at=occurred_at,
            provider_payment_id=obj.get("id"),
            payment_order_id=metadata.get("payment_order_id"),
            amount_minor=int(Decimal(amount.get("value", "0")) * 100) if amount.get("value") else None,
            currency=amount.get("currency"),
            payload=payload,
        )
