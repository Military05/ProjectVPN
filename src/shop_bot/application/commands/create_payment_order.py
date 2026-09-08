from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def create_payment_order(
    container: Any,
    *,
    telegram_id: int,
    tariff_id: int,
    provider: str | None = None,
    idempotency_key: str | None = None,
    username: str | None = None,
    name_or_nick: str | None = None,
) -> Mapping[str, Any]:
    return await container.applications.create_payment.execute(
        telegram_id=telegram_id,
        tariff_id=tariff_id,
        provider=provider,
        idempotency_key=idempotency_key,
        username=username,
        name_or_nick=name_or_nick,
    )
