from __future__ import annotations

import base64
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


class HeleketAdapter(PaymentAdapter):
    provider = "heleket"

    def __init__(self, settings: Settings) -> None:
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

    async def verify_and_normalize_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        del headers
        payload = self._parse_json(raw_body)
        received_sign = payload.get("sign")
        api_key = self.settings.heleket_api_key
        if not api_key or not isinstance(received_sign, str) or not received_sign.strip():
            raise WebhookAuthenticationError("Heleket webhook authentication failed")

        unsigned_payload = dict(payload)
        unsigned_payload.pop("sign", None)
        canonical_json = json.dumps(
            unsigned_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).replace("/", "\\/")
        encoded = base64.b64encode(canonical_json.encode("utf-8")).decode("ascii")
        expected = hashlib.md5((encoded + api_key).encode("utf-8")).hexdigest()  # noqa: S324 - provider contract
        if not hmac.compare_digest(received_sign.strip().lower(), expected):
            raise WebhookAuthenticationError("Heleket webhook authentication failed")

        event_type = str(payload.get("event") or payload.get("type") or "heleket.update")
        raw_status = str(payload.get("status") or payload.get("payment_status") or "pending").lower()
        provider_payment_id = payload.get("uuid") or payload.get("invoice_id") or payload.get("id")
        order_id = payload.get("order_id") or payload.get("merchant_order_id")
        event_key = f"heleket:{provider_payment_id or 'unknown'}:{event_type}:{raw_status}"
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=event_key,
            event_type=event_type,
            status=self._normalize_status(raw_status),
            occurred_at=datetime.now(UTC),
            provider_payment_id=str(provider_payment_id) if provider_payment_id is not None else None,
            payment_order_id=self._optional_int(order_id),
            amount_minor=self._exact_minor(payload.get("amount")),
            currency=self._optional_currency(payload.get("currency")),
            payload=payload,
        )

    @staticmethod
    def _parse_json(raw_body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookPayloadError("Heleket webhook payload is invalid") from exc
        if not isinstance(payload, dict):
            raise WebhookPayloadError("Heleket webhook payload is invalid")
        return payload

    @staticmethod
    def _normalize_status(value: str) -> str:
        if value in {"paid", "success", "completed"}:
            return "paid"
        if value in {"failed", "fail"}:
            return "failed"
        if value in {"expired"}:
            return "expired"
        if value in {"cancelled", "canceled"}:
            return "cancelled"
        return "pending"

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError) as exc:
            raise WebhookPayloadError("Heleket order reference is invalid") from exc

    @staticmethod
    def _exact_minor(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise WebhookPayloadError("Heleket amount is invalid") from exc
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
