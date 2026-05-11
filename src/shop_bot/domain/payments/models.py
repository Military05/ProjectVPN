from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


PaymentStatus = Literal["pending", "authorized", "paid", "failed", "expired", "cancelled"]


@dataclass(slots=True)
class PaymentIntent:
    provider: str
    provider_payment_id: str
    status: PaymentStatus
    payment_url: str | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedWebhookEvent:
    provider: str
    event_key: str
    event_type: str
    status: PaymentStatus
    occurred_at: datetime
    provider_payment_id: str | None = None
    payment_order_id: int | None = None
    amount_minor: int | None = None
    currency: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
