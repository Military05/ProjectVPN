from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from shop_bot.domain.entities.vpn import VpnConfiguration, VpnConfigurationStatus
from shop_bot.domain.vpn.builder import BuiltVlessConfiguration, VlessBuildInput, VlessUriBuilder
from shop_bot.domain.vpn.validation import validate_vless_endpoint


@dataclass(frozen=True, slots=True)
class VpnEndpoint:
    id: int
    protocol: str
    host: str
    port: int
    node_id: int | None
    local_inbound_id: str | None = None
    security: str | None = None
    sni: str | None = None
    fingerprint: str | None = None
    public_key: str | None = None
    short_id: str | None = None
    transport_type: str | None = None
    flow: str | None = None
    encryption: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedVpnAccess:
    configuration: VpnConfiguration
    connection: BuiltVlessConfiguration


class VpnProvisioningService:
    """Creates a VPN aggregate without knowing persistence or remote node APIs."""

    def __init__(
        self,
        builder: VlessUriBuilder,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._builder = builder
        self._uuid_factory = uuid_factory

    def prepare(
        self,
        *,
        subscription_id: int,
        endpoint: VpnEndpoint,
        display_name: str,
    ) -> PreparedVpnAccess:
        validate_vless_endpoint(
            protocol=endpoint.protocol, security=endpoint.security, sni=endpoint.sni,
            fingerprint=endpoint.fingerprint, public_key=endpoint.public_key,
            short_id=endpoint.short_id, flow=endpoint.flow,
        )
        client_uuid = self._uuid_factory()
        connection = self._builder.build(
            VlessBuildInput(
                client_uuid=str(client_uuid),
                host=endpoint.host,
                port=endpoint.port,
                display_name=display_name,
                security=endpoint.security,
                sni=endpoint.sni,
                fingerprint=endpoint.fingerprint,
                public_key=endpoint.public_key,
                short_id=endpoint.short_id,
                transport_type=endpoint.transport_type,
                flow=endpoint.flow,
                encryption=endpoint.encryption,
            )
        )
        configuration = VpnConfiguration(
            id=None,
            subscription_id=subscription_id,
            server_endpoint_id=endpoint.id,
            client_uuid=UUID(str(client_uuid)),
            display_name=display_name,
            status=(
                VpnConfigurationStatus.PROVISIONING
                if endpoint.node_id is not None
                else VpnConfigurationStatus.DISABLED
            ),
        )
        return PreparedVpnAccess(configuration=configuration, connection=connection)
