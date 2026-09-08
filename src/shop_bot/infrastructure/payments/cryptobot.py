from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

import httpx

from shop_bot.core.config import Settings
from shop_bot.core.exceptions import WebhookAuthenticationError, WebhookPayloadError
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent
from shop_bot.infrastructure.payments.base import PaymentAdapter


class CryptoBotAdapter(PaymentAdapter):
    provider = "cryptobot"

    def __init__(self, settings: Settings) -> None:
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

    async def verify_and_normalize_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        token = self.settings.cryptobot_token
        signature = self._header(headers, "crypto-pay-api-signature")
        if not token or not signature:
            raise WebhookAuthenticationError("CryptoBot webhook authentication failed")

        secret_key = hashlib.sha256(token.encode("utf-8")).digest()
        expected = hmac.new(secret_key, raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.lower(), expected):
            raise WebhookAuthenticationError("CryptoBot webhook authentication failed")

        payload = self._parse_json(raw_body)
        invoice_candidate = payload.get("payload") or payload.get("invoice") or payload
        if not isinstance(invoice_candidate, dict):
            raise WebhookPayloadError("CryptoBot invoice payload is invalid")
        invoice = invoice_candidate
        invoice_id = invoice.get("invoice_id") or invoice.get("id")
        invoice_status = str(invoice.get("status") or payload.get("status") or "pending").lower()
        order_id = invoice.get("payload")
        if isinstance(order_id, dict):
            order_id = None
        event_type = str(payload.get("update_type") or f"invoice.{invoice_status}")
        event_key = f"cryptobot:{invoice_id or 'unknown'}:{event_type}:{invoice_status}"
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=event_key,
            event_type=event_type,
            status=self._normalize_status(invoice_status),
            occurred_at=datetime.now(UTC),
            provider_payment_id=str(invoice_id) if invoice_id is not None else None,
            payment_order_id=self._optional_int(order_id),
            amount_minor=self._exact_minor(invoice.get("amount")),
            currency=self._optional_currency(invoice.get("asset") or invoice.get("currency")),
            payload=payload,
        )

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        lowered = name.lower()
        for key, value in headers.items():
            if key.lower() == lowered:
                return value.strip()
        return None

    @staticmethod
    def _parse_json(raw_body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookPayloadError("CryptoBot webhook payload is invalid") from exc
        if not isinstance(payload, dict):
            raise WebhookPayloadError("CryptoBot webhook payload is invalid")
        return payload

    @staticmethod
    def _normalize_status(value: str) -> str:
        aliases = {
            "paid": "paid",
            "active": "pending",
            "pending": "pending",
            "expired": "expired",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "failed": "failed",
        }
        return aliases.get(value.lower(), "pending")

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError) as exc:
            raise WebhookPayloadError("CryptoBot order reference is invalid") from exc

    @staticmethod
    def _exact_minor(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise WebhookPayloadError("CryptoBot amount is invalid") from exc
        integral = decimal_value.to_integral_value()
        if decimal_value != integral:
            return None
        return int(integral)

    @staticmethod
    def _optional_currency(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        return normalized or None
