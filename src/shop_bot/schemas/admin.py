from __future__ import annotations

from pydantic import BaseModel, Field


class CreateTariffRequest(BaseModel):
    tariff_name: str = Field(min_length=1)
    price_minor: int = Field(gt=0)
    currency: str = Field(default="RUB", min_length=1)
    period_days: int = Field(gt=0)
    description: str | None = None
    is_enabled: bool = True


class UpdateTariffRequest(BaseModel):
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