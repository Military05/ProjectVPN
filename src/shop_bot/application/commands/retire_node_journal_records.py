from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def retire_node_journal_records(container: Any) -> Mapping[str, int | str]:
    return await container.applications.retire_node_journal_records.execute()
