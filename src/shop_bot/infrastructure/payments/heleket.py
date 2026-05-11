from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from shop_bot.core.config import Settings
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent
from shop_bot.infrastructure.payments.base import PaymentAdapter


class HeleketAdapter(PaymentAdapter):
    provider = "heleket"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_payment(self, *, order: Mapping[str, Any], return_url: str) -> PaymentIntent:
        if not self.settings.heleket_api_key or not self.settings.heleket_base_url:
            raise RuntimeError("Heleket credentials are not configured")
        payload = {
            "amount": order["amount_minor"],
            "currency": order["currency"],
            "order_id": int(order["payment_order_id"]),
            "callback_url": return_url,
        }
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.heleket_base_url.rstrip('/')}/invoice",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.heleket_api_key}"},
            )
            response.raise_for_status()
            body = response.json()
        invoice = body.get("result", body)
        invoice_id = str(invoice.get("invoice_id") or invoice.get("id") or uuid4().hex)
        return PaymentIntent(
            provider=self.provider,
            provider_payment_id=invoice_id,
            status=invoice.get("status", "pending"),
            payment_url=invoice.get("payment_url") or invoice.get("checkout_url"),
            payload=body,
        )

    def normalize_webhook(
        self,
        *,
        payload: dict[str, Any],
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        event_type = str(payload.get("event") or payload.get("type") or "heleket.update")
        status = str(payload.get("status") or payload.get("payment_status") or "pending")
        provider_payment_id = payload.get("invoice_id") or payload.get("id")
        order_id = payload.get("order_id") or payload.get("merchant_order_id")
        event_key = f"heleket:{provider_payment_id}:{event_type}:{status}"
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=event_key,
            event_type=event_type,
            status="paid" if status in {"paid", "success", "completed"} else status,
            occurred_at=datetime.now(UTC),
            provider_payment_id=str(provider_payment_id) if provider_payment_id is not None else None,
            payment_order_id=int(order_id) if order_id not in (None, "") else None,
            amount_minor=int(payload.get("amount", 0)) if payload.get("amount") is not None else None,
            currency=payload.get("currency"),
            payload=payload,
        )
