from __future__ import annotations

from shop_bot.domain.errors import DomainValidationError


def validate_vless_endpoint(
    *,
    protocol: str,
    security: str | None,
    sni: str | None,
    fingerprint: str | None,
    public_key: str | None,
    short_id: str | None,
    flow: str | None,
) -> None:
    protocol_value = protocol.strip().lower()
    security_value = (security or "none").strip().lower()
    flow_value = (flow or "").strip().lower()
    if protocol_value != "vless":
        raise DomainValidationError("Only VLESS endpoints are supported")
    if security_value == "reality":
        required = {
            "sni": sni,
            "fingerprint": fingerprint,
            "public_key": public_key,
            "short_id": short_id,
        }
        missing = [name for name, value in required.items() if not value or not str(value).strip()]
        if missing:
            raise DomainValidationError("REALITY endpoint requires: " + ", ".join(missing))
    if flow_value == "xtls-rprx-vision" and security_value not in {"tls", "reality"}:
        raise DomainValidationError("xtls-rprx-vision requires tls or reality security")
