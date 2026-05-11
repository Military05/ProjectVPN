from shop_bot.domain.vpn.builder import VlessBuildInput, VlessUriBuilder


def test_vless_uri_builder_includes_only_present_fields() -> None:
    builder = VlessUriBuilder()
    payload = VlessBuildInput(
        client_uuid="123e4567-e89b-12d3-a456-426614174000",
        host="vpn.example.com",
        port=443,
        display_name="premium-1",
        security="reality",
        sni="example.com",
        fingerprint="chrome",
        public_key="pub-key",
        short_id="abcd",
        transport_type="tcp",
        flow="xtls-rprx-vision",
    )

    result = builder.build(payload)

    assert result.uri.startswith("vless://123e4567-e89b-12d3-a456-426614174000@vpn.example.com:443?")
    assert "security=reality" in result.uri
    assert "sni=example.com" in result.uri
    assert "fp=chrome" in result.uri
    assert "pbk=pub-key" in result.uri
    assert "sid=abcd" in result.uri
    assert "flow=xtls-rprx-vision" in result.uri
    assert result.uri.endswith("#premium-1")


def test_vless_uri_builder_falls_back_to_defaults() -> None:
    builder = VlessUriBuilder()
    payload = VlessBuildInput(
        client_uuid="123e4567-e89b-12d3-a456-426614174000",
        host="vpn.example.com",
        port=8443,
        display_name="basic-1",
    )

    result = builder.build(payload)

    assert "type=tcp" in result.uri
    assert "security=none" in result.uri
    assert "encryption=none" in result.uri
    assert "sni=" not in result.uri
