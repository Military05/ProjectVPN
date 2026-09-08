from __future__ import annotations

from typing import Any
from collections.abc import Mapping

from shop_bot.application.revoke_reason import VpnRevokeReason


async def revoke_vpn_configuration(
    container: Any,
    *,
    vpn_configuration_id: int,
    reason: VpnRevokeReason | str = VpnRevokeReason.EXPIRATION,
) -> Mapping[str, str | int]:
    return await container.applications.revoke_vpn.execute(
        vpn_configuration_id=vpn_configuration_id,
        reason=reason,
    )
