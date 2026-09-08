from __future__ import annotations

import pytest
from pydantic import ValidationError

from shop_bot.bootstrap import seed_demo
from shop_bot.core.config import Settings
from shop_bot.infrastructure.payments.registry import PaymentAdapterRegistry


def safe_production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": " production ",
        "internal_api_key": "prod-internal",
        "admin_api_token": "prod-admin",
        "payment_default_provider": "yookassa",
        "yookassa_shop_id": "prod-shop",
        "yookassa_secret_key": "prod-yookassa-secret",
        "panel_mode": "xui",
        "xui_base_url": "https://xui.example.test",
        "xui_username": "prod-xui",
        "xui_password": "prod-xui-secret",
        "database_url": "postgresql+asyncpg://shopbot:prod-db-secret@postgres:5432/shopbot",
        "node_agent_shared_secret": "prod-node-secret",
        "demo_node_shared_secret": "prod-demo-secret",
        "node_agent_runtime_mode": "xui",
        "auto_seed_demo_data": False,
        "demo_node_auto_register": False,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("internal_api_key", "change-me-x"),
        ("admin_api_token", "change-me-x"),
        ("node_agent_shared_secret", "change-me-x"),
        ("default_display_name_prefix", " change-me-future-placeholder "),
    ],
)
def test_production_rejects_change_me_strings(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        safe_production_settings(**{field: value})
    message = str(exc_info.value)
    assert field in message
    assert value.strip() not in message


@pytest.mark.parametrize(
    "override",
    [
        {"payment_default_provider": "dummy"},
        {"node_agent_runtime_mode": "stub"},
        {"panel_mode": "stub"},
        {"auto_seed_demo_data": True},
        {"demo_node_auto_register": True},
    ],
)
def test_production_rejects_unsafe_runtime_modes(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        safe_production_settings(**override)


def test_nonproduction_defaults_remain_usable() -> None:
    assert Settings().is_production is False


def test_safe_production_settings_are_accepted() -> None:
    settings = safe_production_settings()
    assert settings.is_production is True


def test_node_task_lease_must_exceed_request_timeout_with_margin() -> None:
    with pytest.raises(ValidationError):
        Settings(node_request_timeout_seconds=10, node_task_lease_seconds=15)
    assert Settings(node_request_timeout_seconds=10, node_task_lease_seconds=16).node_task_lease_seconds == 16


def test_production_registry_does_not_register_dummy() -> None:
    registry = PaymentAdapterRegistry(safe_production_settings())
    with pytest.raises(ValueError):
        registry.get("dummy")


@pytest.mark.asyncio
async def test_seed_demo_rejects_production_before_engine_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def forbidden_create_engine(settings: Settings) -> object:
        nonlocal called
        called = True
        raise AssertionError("engine must not be created")

    monkeypatch.setattr(seed_demo, "create_engine", forbidden_create_engine)
    with pytest.raises(RuntimeError, match="disabled in production"):
        await seed_demo.seed_demo_data(settings=safe_production_settings())
    assert called is False

@pytest.mark.parametrize("provider", ["cryptobot", "heleket", "ton"])
def test_production_rejects_quarantined_default_payment_providers(provider: str) -> None:
    with pytest.raises(ValidationError, match="quarantined in production"):
        safe_production_settings(payment_default_provider=provider)


def test_production_registry_quarantines_unsafe_external_adapters() -> None:
    registry = PaymentAdapterRegistry(safe_production_settings())
    for provider in ("dummy", "cryptobot", "heleket", "ton"):
        with pytest.raises(ValueError):
            registry.get(provider)
    assert registry.get("yookassa").provider == "yookassa"


def test_nonproduction_registry_keeps_quarantined_adapters_testable() -> None:
    registry = PaymentAdapterRegistry(Settings())
    assert registry.get("cryptobot").provider == "cryptobot"
    assert registry.get("heleket").provider == "heleket"
    assert registry.get("ton").provider == "ton"


@pytest.mark.parametrize(
    ("field", "valid"),
    [
        ("payment_invoice_creation_lease_seconds", 21),
        ("panel_task_lease_seconds", 51),
    ],
)
def test_external_side_effect_leases_exceed_http_timeout_with_margin(field: str, valid: int) -> None:
    invalid = 20 if field == "payment_invoice_creation_lease_seconds" else 50
    with pytest.raises(ValidationError):
        Settings(request_timeout_seconds=15, **{field: invalid})
    assert getattr(Settings(request_timeout_seconds=15, **{field: valid}), field) == valid


def test_node_agent_operation_lease_exceeds_remote_timeout_margin() -> None:
    with pytest.raises(ValidationError):
        Settings(request_timeout_seconds=20, panel_task_lease_seconds=66, node_agent_operation_lease_seconds=65)
    assert Settings(request_timeout_seconds=20, panel_task_lease_seconds=66, node_agent_operation_lease_seconds=66).node_agent_operation_lease_seconds == 66


def test_prod_alias_enables_production_safety() -> None:
    assert safe_production_settings(app_env="prod").is_production is True


def test_production_requires_yookassa_credentials() -> None:
    with pytest.raises(ValidationError, match="YooKassa credentials"):
        safe_production_settings(yookassa_shop_id=None, yookassa_secret_key=None)


def test_production_requires_xui_credentials_for_active_xui_runtime() -> None:
    with pytest.raises(ValidationError, match="XUI credentials"):
        safe_production_settings(xui_password=None)


def test_production_rejects_obvious_database_password() -> None:
    with pytest.raises(ValidationError, match="default database password"):
        safe_production_settings(database_url="postgresql+asyncpg://shopbot:shopbot@postgres:5432/shopbot")
