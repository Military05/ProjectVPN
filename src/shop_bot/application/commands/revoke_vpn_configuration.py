from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from shop_bot.application.commands._helpers import enqueue_job
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow


async def revoke_vpn_configuration(
    container: ServiceContainer,
    *,
    vpn_configuration_id: int,
) -> Mapping[str, str | int]:
    now = utcnow()
    dispatch_node_task_id: int | None = None
    legacy_payload: dict[str, str | int] | None = None

    async with container.uow() as uow:
        config_row = await uow.vpn.get_configuration_with_endpoint(vpn_configuration_id)
        if config_row is None:
            return {"status": "missing"}
        if config_row["status"] in {"revoked", "expired", "disabled"}:
            return {"status": str(config_row["status"]), "vpn_configuration_id": vpn_configuration_id}

        if config_row.get("node_id") is not None:
            node_id = int(config_row["node_id"])
            inbound_id = config_row.get("local_inbound_id") or container.settings.node_agent_inbound_id
            await uow.vpn.mark_configuration_status(vpn_configuration_id, status="revoking")
            node_task = await uow.nodes.create_task(
                node_id=node_id,
                operation="revoke_client",
                idempotency_key=f"revoke:vpn_configuration:{vpn_configuration_id}",
                payload={
                    "task_id": str(uuid4()),
                    "idempotency_key": f"revoke:vpn_configuration:{vpn_configuration_id}",
                    "client_uuid": str(config_row["client_uuid"]),
                    "inbound_id": str(inbound_id),
                    "metadata": {"vpn_configuration_id": vpn_configuration_id},
                },
                vpn_configuration_id=vpn_configuration_id,
                subscription_id=int(config_row["subscription_id"]),
                max_attempts=container.settings.node_task_max_attempts,
            )
            dispatch_node_task_id = int(node_task["node_task_id"])
        else:
            if config_row["status"] != "active":
                return {"status": str(config_row["status"]), "vpn_configuration_id": vpn_configuration_id}
            legacy_payload = {
                "xui_inbound_id": container.settings.xui_inbound_id,
                "client_uuid": str(config_row["client_uuid"]),
            }

    if legacy_payload is not None:
        await container.panel_adapter.revoke(legacy_payload)
        async with container.uow() as uow:
            await uow.vpn.revoke_configuration(vpn_configuration_id, revoked_at=now, status="revoked")
            await uow.payments.create_outbox_event(
                event_name="vpn_configuration_revoked",
                aggregate_type="vpn_configuration",
                aggregate_id=vpn_configuration_id,
                payload={"vpn_configuration_id": vpn_configuration_id},
            )
        await enqueue_job(container, "publish_outbox")
        return {"status": "revoked", "vpn_configuration_id": vpn_configuration_id}

    if dispatch_node_task_id is not None:
        await enqueue_job(container, "dispatch_node_task", dispatch_node_task_id)
        return {
            "status": "queued",
            "vpn_configuration_id": vpn_configuration_id,
            "node_task_id": dispatch_node_task_id,
        }

    raise RuntimeError("Revoke path was not selected")
