import asyncio

import pytest

from shop_bot.apps.node_agent.runtime import StubNodeRuntime
from shop_bot.core.config import Settings


def test_stub_node_runtime_provision_and_revoke_cycle() -> None:
    settings = Settings(node_agent_node_key="de-1")
    runtime = StubNodeRuntime(settings=settings)

    first = asyncio.run(
        runtime.provision_client(
            {
                "task_id": "task-1",
                "idempotency_key": "provision:vpn_configuration:1",
                "client_uuid": "uuid-1",
                "display_name": "vpn-1",
                "inbound_id": "main-vless",
                "flow": "xtls-rprx-vision",
                "metadata": {"vpn_configuration_id": 1},
            }
        )
    )
    duplicate = asyncio.run(
        runtime.provision_client(
            {
                "task_id": "task-2",
                "idempotency_key": "provision:vpn_configuration:1",
                "client_uuid": "uuid-1",
                "display_name": "vpn-1",
                "inbound_id": "main-vless",
                "flow": "xtls-rprx-vision",
                "metadata": {"vpn_configuration_id": 1},
            }
        )
    )
    status_payload = asyncio.run(runtime.status())
    revoked = asyncio.run(
        runtime.revoke_client(
            {
                "task_id": "task-3",
                "idempotency_key": "revoke:vpn_configuration:1",
                "client_uuid": "uuid-1",
                "inbound_id": "main-vless",
                "metadata": {"vpn_configuration_id": 1},
            }
        )
    )
    missing = asyncio.run(
        runtime.revoke_client(
            {
                "task_id": "task-4",
                "idempotency_key": "revoke:vpn_configuration:1",
                "client_uuid": "uuid-1",
                "inbound_id": "main-vless",
                "metadata": {"vpn_configuration_id": 1},
            }
        )
    )

    assert first["status"] == "provisioned"
    assert duplicate["status"] == "already_exists"
    assert status_payload["active_clients"] == 1
    assert revoked["status"] == "revoked"
    assert missing["status"] == "not_found_treated_as_success"


def test_xui_runtime_rejects_http_200_logical_failure(monkeypatch) -> None:
    from shop_bot.apps.node_agent import runtime as runtime_module
    from shop_bot.apps.node_agent.runtime import XuiNodeRuntime

    class Response:
        status_code = 200

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, **kwargs):
            del kwargs
            self.calls += 1
            if url.endswith("/login"):
                return Response({"success": True})
            return Response({"success": False, "msg": "permission denied"})

    monkeypatch.setattr(runtime_module.httpx, "AsyncClient", Client)
    settings = Settings(
        node_agent_runtime_mode="xui",
        xui_base_url="http://xui.local",
        xui_username="user",
        xui_password="secret",
    )
    runtime = XuiNodeRuntime(settings=settings)

    with pytest.raises(RuntimeError, match="XUI logical request failed"):
        asyncio.run(
            runtime.revoke_client(
                {"client_uuid": "uuid-1", "inbound_id": "1"}
            )
        )
