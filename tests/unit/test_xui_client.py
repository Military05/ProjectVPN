from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from shop_bot.core.config import Settings
from shop_bot.infrastructure import xui as xui_module
from shop_bot.infrastructure.xui import XuiClient, inbound_metrics


class Response:
    def __init__(self, payload: Any = None, status_code: int = 200, *, json_error: bool = False) -> None:
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("POST", "https://xui.test")
            raise httpx.HTTPStatusError("failed", request=request, response=httpx.Response(self.status_code, request=request))

    def json(self) -> Any:
        if self.json_error:
            raise ValueError("not json")
        return self.payload


class ScriptedClient:
    scripts: list[tuple[str, Response]] = []
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> "ScriptedClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    @classmethod
    def reset(cls, scripts: list[tuple[str, Response]]) -> None:
        cls.scripts = list(scripts)
        cls.calls = []

    async def _call(self, method: str, url: str, **kwargs: Any) -> Response:
        self.__class__.calls.append((method, url, kwargs))
        expected_method, response = self.__class__.scripts.pop(0)
        assert expected_method == method
        return response

    async def post(self, url: str, **kwargs: Any) -> Response:
        return await self._call("POST", url, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> Response:
        return await self._call("GET", url, **kwargs)


def settings() -> Settings:
    return Settings(xui_base_url="https://xui.test", xui_username="u", xui_password="p")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_response",
    [Response(json_error=True), Response([]), Response({}), Response({"success": False, "msg": "denied"})],
)
async def test_xui_add_rejects_malformed_or_logical_failure(monkeypatch: pytest.MonkeyPatch, bad_response: Response) -> None:
    monkeypatch.setattr(xui_module.httpx, "AsyncClient", ScriptedClient)
    ScriptedClient.reset([("POST", Response({"success": True})), ("POST", bad_response)])
    with pytest.raises(RuntimeError):
        await XuiClient(settings()).add_client(
            inbound_id=7, client_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            email="client", flow=None, expires_at=None,
        )


@pytest.mark.asyncio
async def test_xui_add_propagates_exact_expiry_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xui_module.httpx, "AsyncClient", ScriptedClient)
    ScriptedClient.reset([("POST", Response({"success": True})), ("POST", Response({"success": True}))])
    expires = datetime(2026, 8, 20, 12, 34, 56, 789000, tzinfo=UTC)
    await XuiClient(settings()).add_client(
        inbound_id=7, client_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        email="client", flow="xtls-rprx-vision", expires_at=expires,
    )
    _, url, kwargs = ScriptedClient.calls[1]
    assert url.endswith("/panel/api/inbounds/addClient")
    assert kwargs["json"]["id"] == 7
    assert kwargs["json"]["settings"]["clients"][0]["expiryTime"] == 1787229296789


@pytest.mark.asyncio
async def test_xui_delete_404_requires_authoritative_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xui_module.httpx, "AsyncClient", ScriptedClient)
    client_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    ScriptedClient.reset([
        ("POST", Response({"success": True})),
        ("POST", Response({}, status_code=404)),
        ("GET", Response({"success": True, "obj": {"settings": {"clients": []}}})),
    ])
    assert await XuiClient(settings()).delete_client(inbound_id=7, client_uuid=client_uuid) is True
    assert ScriptedClient.calls[1][1].endswith(f"/panel/api/inbounds/7/delClient/{client_uuid}")

    ScriptedClient.reset([
        ("POST", Response({"success": True})),
        ("POST", Response({}, status_code=404)),
        ("GET", Response({"success": True, "obj": {"settings": {"clients": [{"id": client_uuid}]}}})),
    ])
    assert await XuiClient(settings()).delete_client(inbound_id=7, client_uuid=client_uuid) is False


def test_xui_metrics_use_real_enabled_clients_and_traffic() -> None:
    inbound = {
        "settings": {"clients": [{"id": "a", "enable": True}, {"id": "b", "enable": False}, {"id": "c"}]},
        "clientStats": [{"up": 10, "down": 20}, {"up": "3", "down": "4"}],
    }
    assert inbound_metrics(inbound) == (2, 13, 24)
