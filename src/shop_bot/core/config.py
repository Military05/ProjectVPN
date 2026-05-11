from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    debug: bool = False
    service_mode: Literal["api", "bot", "worker", "node_agent"] = "api"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8080

    database_url: str = "postgresql+asyncpg://shopbot:shopbot@localhost:5432/shopbot"
    redis_url: str = "redis://localhost:6379/0"

    internal_api_key: str = "change-me-internal"
    admin_api_token: str = "change-me-admin"

    bot_token: str = "123456:replace-me"
    bot_public_username: str = "vless_shopbot"
    backend_base_url: AnyHttpUrl = "http://localhost:8080"
    bot_dedup_ttl_seconds: int = 3600

    payment_default_provider: Literal["dummy", "yookassa", "cryptobot", "heleket", "ton"] = "dummy"
    payment_return_url: AnyHttpUrl = "http://localhost:8080/docs"
    default_currency: str = "RUB"
    auto_seed_demo_data: bool = False
    default_display_name_prefix: str = "vpn"

    dummy_payment_base_url: AnyHttpUrl = "http://localhost:8080"

    yookassa_shop_id: str | None = None
    yookassa_secret_key: str | None = None
    yookassa_return_url: AnyHttpUrl = "http://localhost:8080/docs"
    yookassa_webhook_secret: str | None = None

    cryptobot_token: str | None = None
    cryptobot_base_url: AnyHttpUrl = "https://pay.crypt.bot/api"
    cryptobot_webhook_secret: str | None = None

    heleket_api_key: str | None = None
    heleket_base_url: str | None = None
    heleket_webhook_secret: str | None = None

    ton_wallet_address: str | None = None
    ton_webhook_secret: str | None = None

    panel_mode: Literal["stub", "xui"] = "stub"
    xui_base_url: str | None = None
    xui_username: str | None = None
    xui_password: str | None = None
    xui_inbound_id: int = 1

    node_request_timeout_seconds: float = 10.0
    node_http_verify_tls: bool = True
    node_timestamp_tolerance_seconds: int = 90
    node_task_max_attempts: int = 5
    node_task_retry_base_seconds: int = 15
    node_status_sync_interval_seconds: int = 60

    node_agent_node_key: str = "demo-node"
    node_agent_display_name: str = "Demo node"
    node_agent_key_id: str = "default"
    node_agent_shared_secret: str = "change-me-node-secret"
    node_agent_version: str = "1.0.0"
    node_agent_runtime_mode: Literal["stub", "xui"] = "stub"
    node_agent_inbound_id: str = "main-vless"
    node_agent_status_max_clients: int = 500
    node_agent_public_host: str = "vpn.example.local"
    node_agent_public_port: int = 443
    node_agent_protocol: str = "vless"
    node_agent_security: str | None = "reality"
    node_agent_sni: str | None = "vpn.example.local"
    node_agent_fingerprint: str | None = "chrome"
    node_agent_public_key: str | None = "demo-public-key"
    node_agent_short_id: str | None = "12345678"
    node_agent_transport_type: str | None = "tcp"
    node_agent_flow: str | None = "xtls-rprx-vision"
    node_agent_encryption: str | None = "none"

    demo_node_auto_register: bool = False
    demo_node_key: str = "demo-node"
    demo_node_display_name: str = "Demo node"
    demo_node_api_base_url: AnyHttpUrl = "http://node-agent:8090"
    demo_node_key_id: str = "default"
    demo_node_shared_secret: str = "change-me-node-secret"

    sentry_dsn: str | None = None
    otel_exporter_otlp_endpoint: str | None = None

    worker_queue_name: str = "shopbot"
    request_timeout_seconds: float = 15.0
    background_sync_interval_seconds: int = 300
    admin_contact_type: str = Field(default="telegram_id")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
