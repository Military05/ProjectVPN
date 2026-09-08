from __future__ import annotations

from typing import Any
from collections.abc import Mapping


async def provision_vpn_configuration(
    container: Any,
    *,
    subscription_id: int,
) -> Mapping[str, str | int]:
    return await container.applications.provision_vpn.execute(subscription_id=subscription_id)
