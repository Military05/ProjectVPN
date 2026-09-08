from __future__ import annotations

import json
from base64 import b64encode
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from shop_bot.core.config import Settings
from shop_bot.core.exceptions import WebhookAuthenticationError, WebhookPayloadError
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent
from shop_bot.infrastructure.payments.base import PaymentAdapter


class YooKassaAdapter(PaymentAdapter):
    provider = "yookassa"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def create_payment(self, *, order: Mapping[str, Any], return_url: str) -> PaymentIntent:
        del return_url
        if not self.settings.yookassa_shop_id or not self.settings.yookassa_secret_key:
            raise RuntimeError("YooKassa credentials are not configured")
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
                headers={"Authorization": f"Basic {self._basic_auth()}", "Idempotence-Key": str(order["idempotency_key"])},
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

    async def verify_and_normalize_webhook(self, *, raw_body: bytes, headers: Mapping[str, str]) -> NormalizedWebhookEvent:
        del headers
        inbound = self._parse_json(raw_body)
        event_type = str(inbound.get("event") or "payment.unknown")
        inbound_object = inbound.get("object")
        if not isinstance(inbound_object, dict):
            raise WebhookPayloadError("YooKassa webhook object is missing")
        object_id = inbound_object.get("id")
        if not isinstance(object_id, str) or not object_id.strip():
            raise WebhookPayloadError("YooKassa object id is missing")
        if not self.settings.yookassa_shop_id or not self.settings.yookassa_secret_key:
            raise WebhookAuthenticationError("YooKassa webhook authentication failed")

        is_refund = event_type == "refund.succeeded"
        resource = "refunds" if is_refund else "payments"
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.get(
                    f"https://api.yookassa.ru/v3/{resource}/{object_id}",
                    headers={"Authorization": f"Basic {self._basic_auth()}"},
                )
                response.raise_for_status()
                provider_object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WebhookAuthenticationError("YooKassa webhook authentication failed") from exc

        if not isinstance(provider_object, dict) or provider_object.get("id") != object_id:
            raise WebhookAuthenticationError("YooKassa webhook authentication failed")
        if is_refund:
            return self._normalize_refund(provider_object, refund_id=object_id)
        return self._normalize_payment(provider_object, event_type=event_type)

    def _normalize_refund(self, provider_object: dict[str, Any], *, refund_id: str) -> NormalizedWebhookEvent:
        if str(provider_object.get("status") or "").lower() != "succeeded":
            raise WebhookAuthenticationError("YooKassa refund is not succeeded")
        payment_id = provider_object.get("payment_id")
        if not isinstance(payment_id, str) or not payment_id.strip():
            raise WebhookPayloadError("YooKassa refund payment_id is missing")
        amount = provider_object.get("amount")
        if not isinstance(amount, dict):
            raise WebhookPayloadError("YooKassa refund amount is invalid")
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=f"yookassa:refund:{refund_id}:succeeded",
            event_type="refund.succeeded",
            status=None,
            occurred_at=self._occurred_at(provider_object),
            provider_payment_id=payment_id,
            payment_order_id=None,
            amount_minor=self._money_to_minor(amount.get("value")),
            currency=self._optional_currency(amount.get("currency")),
            payload={"event": "refund.succeeded", "object": provider_object},
        )

    def _normalize_payment(self, provider_object: dict[str, Any], *, event_type: str) -> NormalizedWebhookEvent:
        provider_payment_id = str(provider_object["id"])
        raw_status = str(provider_object.get("status") or "pending").lower()
        amount = provider_object.get("amount")
        if amount is not None and not isinstance(amount, dict):
            raise WebhookPayloadError("YooKassa amount is invalid")
        amount = amount or {}
        metadata = provider_object.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise WebhookPayloadError("YooKassa metadata is invalid")
        metadata = metadata or {}
        return NormalizedWebhookEvent(
            provider=self.provider,
            event_key=f"yookassa:{provider_payment_id}:{event_type}:{raw_status}",
            event_type=event_type,
            status=self._normalize_status(raw_status),
            occurred_at=self._occurred_at(provider_object),
            provider_payment_id=provider_payment_id,
            payment_order_id=self._optional_int(metadata.get("payment_order_id")),
            amount_minor=self._money_to_minor(amount.get("value")),
            currency=self._optional_currency(amount.get("currency")),
            payload={"event": event_type, "object": provider_object},
        )

    def _basic_auth(self) -> str:
        if not self.settings.yookassa_shop_id or not self.settings.yookassa_secret_key:
            raise WebhookAuthenticationError("YooKassa credentials are missing")
        return b64encode(f"{self.settings.yookassa_shop_id}:{self.settings.yookassa_secret_key}".encode()).decode("ascii")

    @staticmethod
    def _parse_json(raw_body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookPayloadError("YooKassa webhook payload is invalid") from exc
        if not isinstance(payload, dict):
            raise WebhookPayloadError("YooKassa webhook payload is invalid")
        return payload

    @staticmethod
    def _normalize_status(value: str) -> str:
        return {"succeeded": "paid", "paid": "paid", "pending": "pending", "waiting_for_capture": "authorized", "canceled": "cancelled", "cancelled": "cancelled", "failed": "failed"}.get(value, "pending")

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError) as exc:
            raise WebhookPayloadError("YooKassa order reference is invalid") from exc

    @staticmethod
    def _money_to_minor(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            amount = Decimal(str(value)) * Decimal("100")
        except (InvalidOperation, ValueError) as exc:
            raise WebhookPayloadError("YooKassa amount is invalid") from exc
        integral = amount.to_integral_value()
        if amount != integral:
            raise WebhookPayloadError("YooKassa amount has unsupported precision")
        return int(integral)

    @staticmethod
    def _optional_currency(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        return normalized or None

    @staticmethod
    def _occurred_at(provider_object: dict[str, Any]) -> datetime:
        for field in ("captured_at", "created_at"):
            raw = provider_object.get(field)
            if isinstance(raw, str) and raw:
                try:
                    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
        return datetime.now(UTC)
