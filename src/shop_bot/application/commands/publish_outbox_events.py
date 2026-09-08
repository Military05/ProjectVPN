from __future__ import annotations

from typing import Any
from collections.abc import Mapping


async def publish_outbox_events(container: Any) -> Mapping[str, int]:
    return await container.applications.publish_outbox.execute()
