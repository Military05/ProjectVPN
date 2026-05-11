from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from shop_bot.core.config import Settings
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent
from shop_bot.infrastructure.payments.base import PaymentAdapter


class CryptoBotAdapter(PaymentAdapter):
    provider = "cryptobot"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_payment(self, *, order: Mapping[str, Any], return_url: str) -> PaymentIntent:
        if not self.settings.cryptobot_token:
            raise RuntimeError("CryptoBot token is not configured")
        payload = {
            "asset": order["currency"],
            "amount": str(order["amount_minor"]),
            "description": f"order:{order['payment_order_id']}",
            "paid_btn_name": "openBot",
            "paid_btn_url": return_url,
            "payload": str(order["payment_order_id"]),
        }
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(
                f"{str(self.settings.cryptobot_base_url).rstrip('/')}/createInvoice",
                json=payload,
                headers={"Crypto-Pay-API-Token": self.settings.cryptobot_token},
            )
            response.raise_for_status()
            body = response.json()
        result = body.get("result", body)
        invoice_id = str(result.get("invoice_id") or result.get("id") or uuid4().hex)
        return PaymentIntent(
            provider=self.provider,
            provider_payment_id=invoice_id,
            status=result.get("status", "pending"),
            payment_url=result.get("bot_invoice_url") or result.get("pay_url"),
            payload=body,
        )

    def normalize_webhook(
        self,
        *,
        payload: dict[str, Any],
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        invoice = payload.get("payload") or payload.get("invoice") or payload
        invoice_id = invoice.get("invoice_id") or invoice.get("id")
        invoice_status = invoice.get("status") or payload.get("status") or "pending"
        order_id = invoice.get("payload") or payload.get("payload")
        event_type = payload.get("update_type") or f"invoice.{invoice_status}"
        event_key = f"cryptobot:{invoice_id}:{event_type}:{invoice_status}"
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=event_key,
            event_type=event_type,
            status="paid" if invoice_status == "paid" else invoice_status,
            occurred_at=datetime.now(UTC),
            provider_payment_id=str(invoice_id) if invoice_id is not None else None,
            payment_order_id=int(order_id) if order_id not in (None, "") else None,
            amount_minor=int(float(invoice.get("amount", 0))) if invoice.get("amount") is not None else None,
            currency=invoice.get("asset") or invoice.get("currency"),
            payload=payload,
        )
