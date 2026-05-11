from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from shop_bot.core.config import Settings
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent
from shop_bot.infrastructure.payments.base import PaymentAdapter


class TonAdapter(PaymentAdapter):
    provider = "ton"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_payment(self, *, order: Mapping[str, Any], return_url: str) -> PaymentIntent:
        if not self.settings.ton_wallet_address:
            raise RuntimeError("TON wallet address is not configured")
        provider_payment_id = f"ton-{order['payment_order_id']}-{uuid4().hex[:8]}"
        query = urlencode(
            {
                "amount": order["amount_minor"],
                "text": f"order:{order['payment_order_id']}",
                "bin": return_url,
            }
        )
        payment_url = f"ton://transfer/{self.settings.ton_wallet_address}?{query}"
        return PaymentIntent(
            provider=self.provider,
            provider_payment_id=provider_payment_id,
            status="pending",
            payment_url=payment_url,
            payload={"wallet": self.settings.ton_wallet_address},
        )

    def normalize_webhook(
        self,
        *,
        payload: dict[str, Any],
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        tx_hash = payload.get("tx_hash") or payload.get("transaction_hash") or uuid4().hex
        order_id = payload.get("payment_order_id") or payload.get("order_id")
        status = str(payload.get("status", "paid"))
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=f"ton:{tx_hash}:{status}",
            event_type=payload.get("event_type", "ton.transaction"),
            status=status,
            occurred_at=datetime.now(UTC),
            provider_payment_id=str(tx_hash),
            payment_order_id=int(order_id) if order_id not in (None, "") else None,
            amount_minor=int(payload.get("amount", 0)) if payload.get("amount") is not None else None,
            currency=payload.get("currency"),
            payload=payload,
        )
