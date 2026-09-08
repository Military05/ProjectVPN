from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shop_bot.domain.entities.payment import PaymentEvent


async def process_payment_event(container: Any, *, payment_event_id: int) -> Mapping[str, Any]:
    return await container.applications.process_payment.execute(payment_event_id=payment_event_id)


def _infer_status(event: Mapping[str, Any]) -> str:
    """Backward-compatible helper delegated to the PaymentEvent entity."""
    return str(
        PaymentEvent.infer_payment_status(
            event_type=str(event.get("event_type", "")),
            payload=dict(event.get("payload") or {}),
        )
    )
