from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from shop_bot.core.config import Settings
from shop_bot.core.exceptions import WebhookAuthenticationError
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent
from shop_bot.infrastructure.payments.base import PaymentAdapter


class TonAdapter(PaymentAdapter):
    provider = "ton"

    def __init__(self, settings: Settings) -> None:
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

    async def verify_and_normalize_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        del raw_body, headers
        raise WebhookAuthenticationError(
            "TON webhook authenticity cannot be established with the configured contract"
        )
