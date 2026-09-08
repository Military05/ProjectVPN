from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from urllib.parse import quote, urlencode


@dataclass(slots=True)
class VlessBuildInput:
    client_uuid: str
    host: str
    port: int
    display_name: str
    security: str | None = None
    sni: str | None = None
    fingerprint: str | None = None
    public_key: str | None = None
    short_id: str | None = None
    transport_type: str | None = None
    flow: str | None = None
    encryption: str | None = None


@dataclass(slots=True)
class BuiltVlessConfiguration:
    uri: str


class VlessUriBuilder:
    def build(self, payload: VlessBuildInput) -> BuiltVlessConfiguration:
        query = {
            "type": payload.transport_type or "tcp",
            "security": payload.security or "none",
            "sni": payload.sni,
            "fp": payload.fingerprint,
            "pbk": payload.public_key,
            "sid": payload.short_id,
            "flow": payload.flow,
            "encryption": payload.encryption or "none",
        }
        query = {key: value for key, value in query.items() if value not in (None, "")}
        host = payload.host
        try:
            if ipaddress.ip_address(host).version == 6:
                host = f"[{host}]"
        except ValueError:
            pass
        uri = (
            f"vless://{payload.client_uuid}@{host}:{payload.port}"
            f"?{urlencode(query)}#{quote(payload.display_name, safe='')}"
        )
        return BuiltVlessConfiguration(uri=uri)
