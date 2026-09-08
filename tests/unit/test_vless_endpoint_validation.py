import pytest
from pydantic import ValidationError

from shop_bot.schemas.admin import CreateServerEndpointRequest


def base(**overrides):
    values = dict(server_id=1, protocol="vless", port=443, security="reality", sni="example.com", fingerprint="chrome", public_key="pk", short_id="abcd", flow="xtls-rprx-vision")
    values.update(overrides)
    return values


def test_admin_rejects_non_vless_endpoint() -> None:
    with pytest.raises(ValidationError, match="Only VLESS"):
        CreateServerEndpointRequest(**base(protocol="trojan"))


@pytest.mark.parametrize("field", ["sni", "fingerprint", "public_key", "short_id"])
def test_admin_rejects_incomplete_reality_endpoint(field: str) -> None:
    with pytest.raises(ValidationError, match="REALITY endpoint requires"):
        CreateServerEndpointRequest(**base(**{field: None}))


def test_admin_rejects_vision_without_tls_or_reality() -> None:
    with pytest.raises(ValidationError, match="requires tls or reality"):
        CreateServerEndpointRequest(**base(security="none", flow="xtls-rprx-vision"))


def test_admin_accepts_complete_vless_reality_endpoint() -> None:
    model = CreateServerEndpointRequest(**base())
    assert model.protocol == "vless" and model.security == "reality"
