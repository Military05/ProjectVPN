import asyncio

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
