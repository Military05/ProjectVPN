from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def ingest_webhook_event(
    container: Any,
    *,
    provider: str,
    raw_body: bytes,
    headers: Mapping[str, str],
) -> Mapping[str, Any]:
    return await container.applications.ingest_webhook.execute(
        provider=provider,
        raw_body=raw_body,
        headers=headers,
    )
