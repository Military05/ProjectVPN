from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from shop_bot.application.job_names import JobName
from shop_bot.application.ports import JobQueue, UnitOfWorkFactory
from shop_bot.domain.entities.node import NodeTask, NodeTaskOperation, NodeTaskStatus
from shop_bot.domain.entities.panel_task import PanelProvisionTask, PanelProvisionTaskStatus
from shop_bot.domain.entities.subscription import Subscription
from shop_bot.domain.entities.vpn import VpnConfiguration, VpnConfigurationStatus
from shop_bot.domain.repositories.interfaces import UnitOfWork
from shop_bot.domain.services.vpn_provisioning import VpnEndpoint, VpnProvisioningService
from shop_bot.domain.services.node_selection import NodeAvailabilityPolicy, NodeSelectionCandidate, WeightedNodeSelector


@dataclass(frozen=True, slots=True)
class ProvisionPlan:
    subscription_id: int
    vpn_configuration_id: int
    node_task_id: int | None = None
    panel_task_id: int | None = None
    should_enqueue: bool = True


@dataclass(slots=True)
class ProvisionVpn:
    uow_factory: UnitOfWorkFactory
    provisioning_service: VpnProvisioningService
    job_queue: JobQueue
    display_name_prefix: str
    node_inbound_id: str
    node_task_max_attempts: int
    panel_task_max_attempts: int
    xui_inbound_id: int
    clock: Callable[[], datetime]
    node_health_stale_after_seconds: int = 180

    async def execute(self, *, subscription_id: int) -> dict[str, str | int]:
        plan = await self._prepare_plan(subscription_id)
        if isinstance(plan, dict):
            return plan
        if plan.node_task_id is not None:
            if plan.should_enqueue:
                await self.job_queue.enqueue(JobName.DISPATCH_NODE_TASK, plan.node_task_id)
            return {
                "status": "queued",
                "vpn_configuration_id": plan.vpn_configuration_id,
                "node_task_id": plan.node_task_id,
            }
        if plan.panel_task_id is not None:
            if plan.should_enqueue:
                await self.job_queue.enqueue(JobName.DISPATCH_PANEL_PROVISION_TASK, plan.panel_task_id)
            return {
                "status": "queued",
                "vpn_configuration_id": plan.vpn_configuration_id,
                "panel_task_id": plan.panel_task_id,
            }
        raise RuntimeError("Provisioning path was not selected")

    async def _prepare_plan(self, subscription_id: int) -> ProvisionPlan | dict[str, str | int]:
        async with self.uow_factory() as uow:
            subscription = await uow.subscriptions.get_entity(subscription_id, for_update=True)
            early_result = self._subscription_result(subscription, subscription_id)
            if early_result is not None:
                return early_result

            now = self.clock()
            period = await uow.subscriptions.get_current_period_entity(subscription_id, now)
            if period is None:
                return {"status": "no_current_access", "subscription_id": subscription_id}
            active = await uow.vpn.get_active_entity_for_subscription(subscription_id)
            if active is not None:
                return {"status": "active", "vpn_configuration_id": int(active.id)}

            latest = await uow.vpn.get_latest_entity_for_subscription(subscription_id)
            if latest is not None and latest.status in {
                VpnConfigurationStatus.REVOKING,
                VpnConfigurationStatus.REVOKE_FAILED,
            }:
                return {"status": str(latest.status), "vpn_configuration_id": int(latest.id)}
            if latest is not None and latest.status is VpnConfigurationStatus.PROVISIONING:
                reused = await self._reuse_existing_provisioning(
                    uow,
                    latest=latest,
                    expires_at=period.expires_at,
                    now=now,
                )
                if reused is not None:
                    return reused
                latest.fail_provisioning()
                await uow.vpn.save_entity(latest)

            endpoint_row = await self._select_endpoint(uow, now)
            if endpoint_row is None:
                return {"status": "waiting_for_node_capacity", "subscription_id": subscription_id}
            endpoint = self._endpoint_from_row(endpoint_row)
            configuration = await self._load_or_create_configuration(
                uow,
                subscription_id=subscription_id,
                endpoint=endpoint,
                latest=latest,
            )
            if configuration.id is None:
                raise RuntimeError("VPN configuration was not persisted")
            config_row = await uow.vpn.get_configuration_with_endpoint(configuration.id)
            if config_row is None:
                raise RuntimeError("Failed to load VPN configuration row")

            if endpoint.node_id is None:
                task = await self._create_panel_task(
                    uow,
                    configuration=configuration,
                    config_row=config_row,
                    expires_at=period.expires_at,
                    now=now,
                )
                if task.id is None:
                    raise RuntimeError("Panel provision task was not persisted")
                return ProvisionPlan(
                    subscription_id=subscription_id,
                    vpn_configuration_id=configuration.id,
                    panel_task_id=task.id,
                    should_enqueue=task.status is PanelProvisionTaskStatus.PENDING,
                )

            task = await self._create_node_task(
                uow,
                configuration=configuration,
                endpoint=endpoint,
                config_row=config_row,
                expires_at=period.expires_at,
                now=now,
            )
            if task.id is None:
                raise RuntimeError("Node task was not persisted")
            return ProvisionPlan(
                subscription_id=subscription_id,
                vpn_configuration_id=configuration.id,
                node_task_id=task.id,
                should_enqueue=task.status is NodeTaskStatus.PENDING,
            )

    async def _select_endpoint(self, uow: UnitOfWork, now: datetime) -> Mapping[str, Any] | None:
        if hasattr(uow.servers, "list_node_selection_candidates"):
            rows = await uow.servers.list_node_selection_candidates(now=now, stale_after_seconds=self.node_health_stale_after_seconds)
            candidates = []
            for row in rows:
                if row.get("node_id") is None:
                    continue
                candidates.append(NodeSelectionCandidate(
                    node_id=int(row["node_id"]), endpoint_id=int(row["server_endpoint_id"]),
                    node_status=str(row.get("health_status") or row.get("status") or "unknown"),
                    is_enabled=bool(row.get("is_enabled", row.get("node_is_enabled", False))),
                    last_checked_at=row.get("last_checked_at"), active_clients=row.get("active_clients"),
                    max_clients=row.get("max_clients"), selection_weight=int(row.get("selection_weight") or 1),
                ))
            selected = WeightedNodeSelector().select(candidates, now=now, policy=NodeAvailabilityPolicy(self.node_health_stale_after_seconds))
            if selected is not None:
                for row in rows:
                    if int(row.get("server_endpoint_id")) == selected.endpoint_id:
                        return row
            # Preserve panel-only deployments when no node candidates exist.
            if not candidates:
                return await uow.servers.get_first_enabled_endpoint()
            return None
        return await uow.servers.get_first_enabled_endpoint()

    async def _reuse_existing_provisioning(
        self,
        uow: UnitOfWork,
        *,
        latest: VpnConfiguration,
        expires_at: datetime,
        now: datetime,
    ) -> ProvisionPlan | dict[str, str | int] | None:
        if latest.id is None:
            raise RuntimeError("Persisted VPN configuration has no id")
        config_row = await uow.vpn.get_configuration_with_endpoint(latest.id)
        if config_row is None:
            raise RuntimeError("Failed to load existing provisioning configuration")
        endpoint = self._endpoint_from_row(config_row)
        if endpoint.node_id is not None:
            task = await uow.nodes.get_vpn_task_for_generation(
                latest.id,
                str(NodeTaskOperation.PROVISION_CLIENT),
                latest.generation,
            )
            if task is None:
                task = await self._create_node_task(
                    uow,
                    configuration=latest,
                    endpoint=endpoint,
                    config_row=config_row,
                    expires_at=expires_at,
                    now=now,
                )
            elif task.status in {NodeTaskStatus.FAILED, NodeTaskStatus.CANCELLED}:
                return {"status": "cleanup_required", "vpn_configuration_id": latest.id}
            if task.id is None:
                raise RuntimeError("Existing node task has no id")
            return ProvisionPlan(
                subscription_id=latest.subscription_id,
                vpn_configuration_id=latest.id,
                node_task_id=task.id,
                should_enqueue=task.status is NodeTaskStatus.PENDING,
            )

        task = await uow.vpn.get_panel_task_for_configuration(latest.id)
        if task is None:
            task = await self._create_panel_task(
                uow,
                configuration=latest,
                config_row=config_row,
                expires_at=expires_at,
                now=now,
            )
        if task.id is None:
            raise RuntimeError("Existing panel provision task has no id")
        if task.status is PanelProvisionTaskStatus.FAILED:
            return None
        return ProvisionPlan(
            subscription_id=latest.subscription_id,
            vpn_configuration_id=latest.id,
            panel_task_id=task.id,
            should_enqueue=task.status is PanelProvisionTaskStatus.PENDING,
        )

    @staticmethod
    def _subscription_result(subscription: Subscription | None, subscription_id: int) -> dict[str, str | int] | None:
        if subscription is None:
            return {"status": "missing"}
        if str(subscription.status) != "active":
            return {"status": str(subscription.status), "subscription_id": subscription_id}
        return None

    async def _load_or_create_configuration(
        self,
        uow: UnitOfWork,
        *,
        subscription_id: int,
        endpoint: VpnEndpoint,
        latest: VpnConfiguration | None,
    ) -> VpnConfiguration:
        reusable_statuses = {
            VpnConfigurationStatus.REVOKED,
            VpnConfigurationStatus.EXPIRED,
            VpnConfigurationStatus.DISABLED,
        }
        if latest is not None and latest.status not in reusable_statuses:
            if latest.id is None:
                raise RuntimeError("Persisted VPN configuration has no id")
            return latest

        prepared = self.provisioning_service.prepare(
            subscription_id=subscription_id,
            endpoint=endpoint,
            display_name=f"{self.display_name_prefix}-{subscription_id}",
        )
        # Both node and legacy-panel paths are asynchronous durable operations now.
        prepared.configuration.status = VpnConfigurationStatus.PROVISIONING
        configuration = await uow.vpn.add_entity(prepared.configuration)
        if configuration.id is None:
            raise RuntimeError("VPN configuration was not persisted")
        await uow.payments.create_outbox_event(
            event_name="vpn_configuration_created",
            aggregate_type="vpn_configuration",
            aggregate_id=configuration.id,
            payload={"vpn_configuration_id": configuration.id, "subscription_id": subscription_id},
        )
        return configuration

    async def _create_node_task(
        self,
        uow: UnitOfWork,
        *,
        configuration: VpnConfiguration,
        endpoint: VpnEndpoint,
        config_row: Mapping[str, Any],
        expires_at: datetime,
        now: datetime,
    ) -> NodeTask:
        if configuration.id is None or endpoint.node_id is None:
            raise RuntimeError("Node provisioning requires persisted configuration and node")
        task_uuid = uuid4()
        idempotency_key = (
            f"provision:vpn_configuration:{configuration.id}:"
            f"generation:{configuration.generation}:task:{task_uuid}"
        )
        payload = {
            "task_id": str(task_uuid),
            "idempotency_key": idempotency_key,
            "client_uuid": str(config_row["client_uuid"]),
            "display_name": str(config_row["display_name"]),
            "inbound_id": str(endpoint.local_inbound_id or self.node_inbound_id),
            "flow": config_row.get("flow"),
            "expires_at": expires_at.isoformat(),
            "metadata": {
                "vpn_configuration_id": configuration.id,
                "vpn_generation": configuration.generation,
            },
        }
        return await uow.nodes.add_task_entity(
            NodeTask(
                id=None,
                task_uuid=task_uuid,
                node_id=endpoint.node_id,
                operation=NodeTaskOperation.PROVISION_CLIENT,
                status=NodeTaskStatus.PENDING,
                idempotency_key=idempotency_key,
                payload=payload,
                vpn_configuration_id=configuration.id,
                subscription_id=configuration.subscription_id,
                vpn_generation=configuration.generation,
                max_attempts=self.node_task_max_attempts,
                next_retry_at=now,
            )
        )

    async def _create_panel_task(
        self,
        uow: UnitOfWork,
        *,
        configuration: VpnConfiguration,
        config_row: Mapping[str, Any],
        expires_at: datetime,
        now: datetime,
    ) -> PanelProvisionTask:
        if configuration.id is None:
            raise RuntimeError("Panel provisioning requires persisted configuration")
        return await uow.vpn.add_panel_task_entity(
            PanelProvisionTask(
                id=None,
                vpn_configuration_id=configuration.id,
                subscription_id=configuration.subscription_id,
                status=PanelProvisionTaskStatus.PENDING,
                idempotency_key=f"panel-provision:vpn_configuration:{configuration.id}",
                payload=self._panel_payload(config_row, expires_at),
                vpn_generation=configuration.generation,
                max_attempts=self.panel_task_max_attempts,
                next_retry_at=now,
            )
        )

    @staticmethod
    def _endpoint_from_row(row: Mapping[str, Any]) -> VpnEndpoint:
        return VpnEndpoint(
            id=int(row["server_endpoint_id"]),
            protocol=str(row.get("protocol") or "vless"),
            host=str(row["host"]),
            port=int(row["port"]),
            node_id=int(row["node_id"]) if row.get("node_id") is not None else None,
            local_inbound_id=row.get("local_inbound_id"),
            security=row.get("security"),
            sni=row.get("sni"),
            fingerprint=row.get("fingerprint"),
            public_key=row.get("public_key"),
            short_id=row.get("short_id"),
            transport_type=row.get("transport_type"),
            flow=row.get("flow"),
            encryption=row.get("encryption"),
        )

    def _panel_payload(self, config_row: Mapping[str, Any], expires_at: datetime) -> dict[str, Any]:
        local_inbound_id = config_row.get("local_inbound_id")
        inbound_id = local_inbound_id if local_inbound_id not in (None, "") else self.xui_inbound_id
        return {
            "xui_inbound_id": inbound_id,
            "client_uuid": str(config_row["client_uuid"]),
            "display_name": str(config_row["display_name"]),
            "flow": config_row.get("flow"),
            "expires_at": expires_at.isoformat(),
        }
