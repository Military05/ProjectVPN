from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from shop_bot.domain.vpn.builder import BuiltVlessConfiguration, VlessBuildInput, VlessUriBuilder


@dataclass(slots=True)
class PreparedVpnConfiguration:
    client_uuid: str
    display_name: str
    built: BuiltVlessConfiguration


class VpnService:
    def __init__(self, builder: VlessUriBuilder):
        self.builder = builder

    def prepare_configuration(
        self,
        *,
        host: str,
        port: int,
        display_name: str,
        security: str | None,
        sni: str | None,
        fingerprint: str | None,
        public_key: str | None,
        short_id: str | None,
        transport_type: str | None,
        flow: str | None,
        encryption: str | None,
    ) -> PreparedVpnConfiguration:
        client_uuid = str(uuid4())
        built = self.builder.build(
            VlessBuildInput(
                client_uuid=client_uuid,
                host=host,
                port=port,
                display_name=display_name,
                security=security,
                sni=sni,
                fingerprint=fingerprint,
                public_key=public_key,
                short_id=short_id,
                transport_type=transport_type,
                flow=flow,
                encryption=encryption,
            )
        )
        return PreparedVpnConfiguration(client_uuid=client_uuid, display_name=display_name, built=built)
