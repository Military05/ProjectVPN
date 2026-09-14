from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def publish_outbox_events(container: Any) -> Mapping[str, str]:
    """Compatibility tombstone for Redis jobs queued before CHANGE-06."""

    del container
    return {"status": "deprecated_noop"}
