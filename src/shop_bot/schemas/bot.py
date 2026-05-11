from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BotRegisterRequest(BaseModel):
    telegram_id: int
    username: str | None = None
    name_or_nick: str


class BotUserResponse(BaseModel):
    user_id: int
    name_or_nick: str


class TariffResponse(BaseModel):
    tariff_id: int
    tariff_name: str
    price_minor: int
    currency: str
    period_days: int
    description: str | None = None
    is_enabled: bool


class BotCreateOrderRequest(BaseModel):
    telegram_id: int
    tariff_id: int
    provider: str | None = None
    idempotency_key: str | None = None
    username: str | None = None
    name_or_nick: str | None = None


class BotCreateOrderResponse(BaseModel):
    payment_order_id: int
    provider: str
    status: str
    amount_minor: int
    currency: str
    payment_url: str | None = None
    provider_payment_id: str | None = None
    tariff_name: str


class DashboardSubscriptionResponse(BaseModel):
    subscription_id: int
    tariff_id: int
    tariff_name: str
    status: str
    starts_at: datetime
    expires_at: datetime


class DashboardVpnConfigurationResponse(BaseModel):
    vpn_configuration_id: int
    status: str
    display_name: str
    client_uuid: str
    server_name: str
    host: str
    port: int
    uri: str | None = None
    subscription_url: str | None = None


class BotDashboardResponse(BaseModel):
    user: BotUserResponse | None = None
    subscription: DashboardSubscriptionResponse | None = None
    vpn_configuration: DashboardVpnConfigurationResponse | None = None