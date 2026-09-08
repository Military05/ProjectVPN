from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from shop_bot.domain.errors import DomainValidationError
from shop_bot.domain.vpn.validation import validate_vless_endpoint


class CreateTariffRequest(BaseModel):
    tariff_name: str = Field(min_length=1)
    price_minor: int = Field(gt=0)
    currency: str = Field(default="RUB", min_length=1)
    period_days: int = Field(gt=0)
    description: str | None = None
    is_enabled: bool = True


class CreateServerRequest(BaseModel):
    server_name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    is_enabled: bool = True


class CreateServerEndpointRequest(BaseModel):
    server_id: int
    protocol: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    node_id: int | None = None
    local_inbound_id: str | None = None
    security: str | None = None
    sni: str | None = None
    fingerprint: str | None = None
    public_key: str | None = None
    short_id: str | None = None
    transport_type: str | None = None
    flow: str | None = None
    encryption: str | None = None
    is_enabled: bool = True

    @model_validator(mode="after")
    def validate_endpoint(self) -> "CreateServerEndpointRequest":
        self.protocol = self.protocol.strip().lower()
        self.security = self.security.strip().lower() if self.security else self.security
        self.flow = self.flow.strip().lower() if self.flow else self.flow
        try:
            validate_vless_endpoint(
                protocol=self.protocol,
                security=self.security,
                sni=self.sni,
                fingerprint=self.fingerprint,
                public_key=self.public_key,
                short_id=self.short_id,
                flow=self.flow,
            )
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self
