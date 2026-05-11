from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from shop_bot.application.commands._helpers import enqueue_job
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow


async def provision_vpn_configuration(
    container: ServiceContainer,
    *,
    subscription_id: int,
) -> Mapping[str, str | int]:
    dispatch_node_task_id: int | None = None
    legacy_payload: dict[str, Any] | None = None
    vpn_configuration_id: int | None = None

    async with container.uow() as uow:
        subscription = await uow.subscriptions.get_subscription(subscription_id)
        if subscription is None:
            return {"status": "missing"}
        if subscription["status"] != "active":
            return {"status": str(subscription["status"]), "subscription_id": subscription_id}

        now = utcnow()
        current_period = await uow.subscriptions.get_current_period(subscription_id, now)
        if current_period is None:
            return {"status": "no_current_access", "subscription_id": subscription_id}

        active_config = await uow.vpn.get_active_configuration_for_subscription(subscription_id)
        if active_config is not None:
            return {
                "status": "active",
                "vpn_configuration_id": int(active_config["vpn_configuration_id"]),
            }

        latest_config = await uow.vpn.get_latest_configuration_for_subscription(subscription_id)
        if latest_config is not None and latest_config["status"] in {"provisioning", "revoking"}:
            return {
                "status": str(latest_config["status"]),
                "vpn_configuration_id": int(latest_config["vpn_configuration_id"]),
            }

        endpoint = await uow.servers.get_first_enabled_endpoint()
        if endpoint is None:
            raise RuntimeError("No enabled server endpoint is configured")

        if latest_config is None or latest_config["status"] in {
            "revoked",
            "expired",
            "disabled",
            "failed",
            "revoke_failed",
        }:
            display_name = f"{container.settings.default_display_name_prefix}-{subscription_id}"
            prepared = container.vpn_service.prepare_configuration(
                host=str(endpoint["host"]),
                port=int(endpoint["port"]),
                display_name=display_name,
                security=endpoint["security"],
                sni=endpoint["sni"],
                fingerprint=endpoint["fingerprint"],
                public_key=endpoint["public_key"],
                short_id=endpoint["short_id"],
                transport_type=endpoint["transport_type"],
                flow=endpoint["flow"],
                encryption=endpoint["encryption"],
            )
            vpn_configuration_id = await uow.vpn.create_configuration(
                subscription_id=subscription_id,
                server_endpoint_id=int(endpoint["server_endpoint_id"]),
                client_uuid=UUID(prepared.client_uuid),
                display_name=prepared.display_name,
                status="provisioning" if endpoint.get("node_id") is not None else "disabled",
            )
            config_row = await uow.vpn.get_configuration_with_endpoint(vpn_configuration_id)
            await uow.payments.create_outbox_event(
                event_name="vpn_configuration_created",
                aggregate_type="vpn_configuration",
                aggregate_id=vpn_configuration_id,
                payload={"vpn_configuration_id": vpn_configuration_id, "subscription_id": subscription_id},
            )
        else:
            vpn_configuration_id = int(latest_config["vpn_configuration_id"])
            config_row = await uow.vpn.get_configuration_with_endpoint(vpn_configuration_id)

        if config_row is None:
            raise RuntimeError("Failed to load VPN configuration row")

        if endpoint.get("node_id") is not None:
            node_id = int(endpoint["node_id"])
            inbound_id = endpoint.get("local_inbound_id") or container.settings.node_agent_inbound_id
            task_payload = {
                "task_id": str(uuid4()),
                "idempotency_key": f"provision:vpn_configuration:{vpn_configuration_id}",
                "client_uuid": str(config_row["client_uuid"]),
                "display_name": str(config_row["display_name"]),
                "inbound_id": str(inbound_id),
                "flow": config_row.get("flow"),
                "expires_at": current_period["expires_at"].isoformat(),
                "metadata": {"vpn_configuration_id": vpn_configuration_id},
            }
            node_task = await uow.nodes.create_task(
                node_id=node_id,
                operation="provision_client",
                idempotency_key=f"provision:vpn_configuration:{vpn_configuration_id}",
                payload=task_payload,
                vpn_configuration_id=vpn_configuration_id,
                subscription_id=subscription_id,
                max_attempts=container.settings.node_task_max_attempts,
            )
            dispatch_node_task_id = int(node_task["node_task_id"])
        else:
            legacy_payload = _build_panel_payload(container, config_row)

    if legacy_payload is not None:
        await container.panel_adapter.provision(legacy_payload)
        async with container.uow() as uow:
            await uow.vpn.activate_configuration(vpn_configuration_id)
            await uow.payments.create_outbox_event(
                event_name="vpn_configuration_activated",
                aggregate_type="vpn_configuration",
                aggregate_id=vpn_configuration_id,
                payload={"vpn_configuration_id": vpn_configuration_id, "subscription_id": subscription_id},
            )
        await enqueue_job(container, "publish_outbox")
        return {"status": "activated", "vpn_configuration_id": vpn_configuration_id}

    if dispatch_node_task_id is not None:
        await enqueue_job(container, "dispatch_node_task", dispatch_node_task_id)
        return {
            "status": "queued",
            "vpn_configuration_id": vpn_configuration_id,
            "node_task_id": dispatch_node_task_id,
        }

    raise RuntimeError("Provisioning path was not selected")


def _build_panel_payload(container: ServiceContainer, config_row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "xui_inbound_id": container.settings.xui_inbound_id,
        "client_uuid": str(config_row["client_uuid"]),
        "display_name": str(config_row["display_name"]),
        "flow": config_row.get("flow"),
    }
