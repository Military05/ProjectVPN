from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse, unquote

from pydantic import AnyHttpUrl, Field, model_validator
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
    webhook_max_body_bytes: int = 262144
    trusted_proxy_cidrs: list[str] = []
    yookassa_webhook_allowed_networks: list[str] = [
        "185.71.76.0/27", "185.71.77.0/27", "77.75.153.0/25",
        "77.75.156.11/32", "77.75.156.35/32", "77.75.154.128/25", "2a02:5180::/32",
    ]

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
    node_task_lease_seconds: int = 60
    payment_invoice_creation_lease_seconds: int = 30
    panel_task_max_attempts: int = 5
    panel_task_retry_base_seconds: int = 15
    panel_task_lease_seconds: int = 60
    node_status_sync_interval_seconds: int = 60
    node_status_sync_concurrency: int = 10
    node_status_sync_batch_size: int = 100
    node_status_probe_lease_seconds: int = 30
    node_health_stale_after_seconds: int = 180
    node_unavailable_retry_seconds: int = 30

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
    node_agent_idempotency_db_path: str = "/var/lib/shopbot-node-agent/idempotency.sqlite3"
    node_agent_operation_lease_seconds: int = 60
    node_agent_idempotency_retention_days: int = 30

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
    reconciliation_batch_size: int = 500
    reconciliation_max_batches_per_run: int = 4
    reconciliation_lease_seconds: int = 120
    admin_contact_type: str = Field(default="telegram_id")

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"prod", "production"}

    @property
    def node_agent_xui_inbound_id(self) -> int | None:
        if self.node_agent_runtime_mode != "xui":
            return None
        return int(self.node_agent_inbound_id)

    @model_validator(mode="after")
    def validate_runtime_safety(self) -> "Settings":
        self._validate_leases()
        self._validate_scheduler_settings()
        self._validate_xui_contracts()
        self._validate_production_guardrails()
        return self

    def _validate_leases(self) -> None:
        if self.node_task_lease_seconds <= 0:
            raise ValueError("node_task_lease_seconds must be positive")
        minimum_lease = float(self.node_request_timeout_seconds) + 5.0
        if float(self.node_task_lease_seconds) <= minimum_lease:
            raise ValueError(
                "node_task_lease_seconds must be greater than "
                "node_request_timeout_seconds + 5 seconds"
            )

        lease_requirements = (
            ("payment_invoice_creation_lease_seconds", self.payment_invoice_creation_lease_seconds, float(self.request_timeout_seconds) + 5.0),
            ("panel_task_lease_seconds", self.panel_task_lease_seconds, 3.0 * float(self.request_timeout_seconds) + 5.0),
            ("node_agent_operation_lease_seconds", self.node_agent_operation_lease_seconds, 3.0 * float(self.request_timeout_seconds) + 5.0),
        )
        for field_name, value, minimum in lease_requirements:
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")
            if float(value) <= minimum:
                raise ValueError(f"{field_name} must be greater than {minimum:g} seconds")
        if self.webhook_max_body_bytes <= 0:
            raise ValueError("webhook_max_body_bytes must be positive")
        if self.panel_task_max_attempts <= 0:
            raise ValueError("panel_task_max_attempts must be positive")
        if self.panel_task_retry_base_seconds <= 0:
            raise ValueError("panel_task_retry_base_seconds must be positive")

    def _validate_scheduler_settings(self) -> None:
        for name in ("node_status_sync_interval_seconds", "node_status_sync_concurrency", "node_status_sync_batch_size", "node_status_probe_lease_seconds", "node_health_stale_after_seconds", "node_unavailable_retry_seconds", "reconciliation_batch_size", "reconciliation_max_batches_per_run", "reconciliation_lease_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.node_health_stale_after_seconds < 2 * self.node_status_sync_interval_seconds + self.node_request_timeout_seconds:
            raise ValueError("node_health_stale_after_seconds must cover two sync intervals and request timeout")

    def _validate_xui_contracts(self) -> None:
        if self.node_agent_runtime_mode == "xui":
            value = self.node_agent_inbound_id.strip()
            try:
                inbound_id = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("NODE_AGENT_INBOUND_ID must be a positive integer in xui mode") from exc
            if inbound_id <= 0:
                raise ValueError("NODE_AGENT_INBOUND_ID must be a positive integer in xui mode")
        if (self.panel_mode == "xui" or self.node_agent_runtime_mode == "xui") and (
            not self.xui_base_url or not self.xui_username or not self.xui_password
        ) and self.is_production:
            raise ValueError("XUI credentials are missing")

    def _validate_production_guardrails(self) -> None:

        if not self.is_production:
            return

        placeholder_fields = sorted(
            name
            for name, value in self.model_dump().items()
            if isinstance(value, str) and value.strip().lower().startswith("change-me-")
        )
        errors: list[str] = []
        if placeholder_fields:
            errors.append("change-me placeholders: " + ", ".join(placeholder_fields))
        if self.payment_default_provider == "dummy":
            errors.append("dummy payment provider")
        if self.payment_default_provider == "yookassa" and (not self.yookassa_shop_id or not self.yookassa_secret_key):
            errors.append("YooKassa credentials are missing")
        if (self.panel_mode == "xui" or self.node_agent_runtime_mode == "xui") and (
            not self.xui_base_url or not self.xui_username or not self.xui_password
        ):
            errors.append("XUI credentials are missing")
        try:
            parsed_db = urlparse(self.database_url.replace("+asyncpg", ""))
            db_password = unquote(parsed_db.password or "").strip().lower()
            if db_password in {"shopbot", "password", "postgres", "change-me", "changeme", "replace-me"}:
                errors.append("obvious default database password")
        except ValueError:
            errors.append("invalid database URL")
        if self.payment_default_provider in {"cryptobot", "heleket", "ton"}:
            errors.append(f"{self.payment_default_provider} payment provider is quarantined in production")
        if self.node_agent_runtime_mode == "stub":
            errors.append("stub node agent runtime")
        if self.panel_mode == "stub":
            errors.append("stub panel runtime")
        if self.auto_seed_demo_data:
            errors.append("auto demo seed enabled")
        if self.demo_node_auto_register:
            errors.append("demo node auto-register enabled")
        if errors:
            raise ValueError("Unsafe production configuration: " + "; ".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
