from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent


class PaymentAdapter(ABC):
    provider: str

    @abstractmethod
    async def create_payment(
        self,
        *,
        order: Mapping[str, Any],
        return_url: str,
    ) -> PaymentIntent:
        raise NotImplementedError

    @abstractmethod
    async def verify_and_normalize_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent:
        raise NotImplementedError
