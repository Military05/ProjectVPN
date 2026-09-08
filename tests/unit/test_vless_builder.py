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


def test_vless_uri_builder_brackets_ipv6_and_escapes_fragment() -> None:
    result = VlessUriBuilder().build(VlessBuildInput(
        client_uuid="123e4567-e89b-12d3-a456-426614174000",
        host="2001:db8::1", port=443, display_name="name #?/ Привет",
    ))
    assert "@[2001:db8::1]:443?" in result.uri
    assert result.uri.endswith("#name%20%23%3F%2F%20%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82")
