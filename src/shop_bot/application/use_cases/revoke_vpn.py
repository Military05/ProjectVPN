from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from shop_bot.application.job_names import JobName
from shop_bot.application.ports import JobQueue, UnitOfWorkFactory
from shop_bot.application.revoke_reason import VpnRevokeReason
from shop_bot.domain.entities.node import NodeTask, NodeTaskOperation, NodeTaskStatus
from shop_bot.domain.entities.panel_task import PanelRevokeTask, PanelRevokeTaskStatus
from shop_bot.domain.entities.vpn import VpnConfigurationStatus, VpnDesiredState
from shop_bot.domain.repositories.interfaces import UnitOfWork


@dataclass(frozen=True, slots=True)
class RevokePlan:
    vpn_configuration_id: int
    node_task_id: int | None = None
    panel_task_id: int | None = None
    should_enqueue: bool = True


@dataclass(slots=True)
class RevokeVpn:
    uow_factory: UnitOfWorkFactory
    job_queue: JobQueue
    node_inbound_id: str
    node_task_max_attempts: int
    panel_task_max_attempts: int
    xui_inbound_id: int
    clock: Callable[[], datetime]

    async def execute(
        self,
        *,
        vpn_configuration_id: int,
        reason: VpnRevokeReason | str = VpnRevokeReason.EXPIRATION,
    ) -> dict[str, str | int]:
        reason = VpnRevokeReason(reason)
        now = self.clock()
        plan = await self._prepare_plan(
            vpn_configuration_id=vpn_configuration_id,
            reason=reason,
            now=now,
        )
        if isinstance(plan, dict):
            return plan
        if plan.node_task_id is not None:
            if plan.should_enqueue:
                await self.job_queue.enqueue(JobName.DISPATCH_NODE_TASK, plan.node_task_id)
            return {
                "status": "queued",
                "vpn_configuration_id": vpn_configuration_id,
                "node_task_id": plan.node_task_id,
            }
        if plan.panel_task_id is not None:
            if plan.should_enqueue:
                await self.job_queue.enqueue(JobName.DISPATCH_PANEL_REVOKE_TASK, plan.panel_task_id)
            return {
                "status": "queued",
                "vpn_configuration_id": vpn_configuration_id,
                "panel_task_id": plan.panel_task_id,
            }
        raise RuntimeError("Revoke path was not selected")

    async def _prepare_plan(
        self,
        *,
        vpn_configuration_id: int,
        reason: VpnRevokeReason,
        now: datetime,
    ) -> RevokePlan | dict[str, str | int]:
        # Candidate read supplies immutable routing/subscription identifiers only.
        # The authoritative state decision below is made after the required row locks.
        async with self.uow_factory() as uow:
            config_row = await uow.vpn.get_configuration_with_endpoint(vpn_configuration_id)
            if config_row is None:
                return {"status": "missing"}
            subscription_id = int(config_row["subscription_id"])

            if reason is VpnRevokeReason.EXPIRATION:
                subscription = await uow.subscriptions.get_entity(subscription_id, for_update=True)
                if subscription is None:
                    return {"status": "missing_subscription", "vpn_configuration_id": vpn_configuration_id}
                current_period = await uow.subscriptions.get_current_period_entity(subscription_id, now)
                if str(subscription.status) == "active" and current_period is not None:
                    return {
                        "status": "skipped_current_access",
                        "vpn_configuration_id": vpn_configuration_id,
                    }

            configuration = await uow.vpn.get_entity(vpn_configuration_id, for_update=True)
            if configuration is None:
                return {"status": "missing"}

            existing = await self._existing_current_revoke_task(uow, configuration, config_row)
            if configuration.status is VpnConfigurationStatus.REVOKING and existing is not None:
                return existing
            if (
                configuration.status is VpnConfigurationStatus.FAILED
                and configuration.desired_state is VpnDesiredState.REVOKED
                and existing is not None
            ):
                return existing

            if configuration.is_terminal:
                return {
                    "status": str(configuration.status),
                    "vpn_configuration_id": vpn_configuration_id,
                }

            if reason is VpnRevokeReason.EXPIRATION:
                if configuration.status is not VpnConfigurationStatus.ACTIVE:
                    return {
                        "status": str(configuration.status),
                        "vpn_configuration_id": vpn_configuration_id,
                    }
                configuration.begin_revoke()
            else:
                if configuration.status is VpnConfigurationStatus.ACTIVE:
                    configuration.begin_revoke()
                elif configuration.status is VpnConfigurationStatus.REVOKE_FAILED:
                    configuration.retry_revoke()
                elif configuration.status is VpnConfigurationStatus.PROVISIONING:
                    configuration.cancel_provisioning_for_revoke()
                elif configuration.status is VpnConfigurationStatus.FAILED:
                    configuration.request_cleanup_from_failed()
                elif configuration.status is VpnConfigurationStatus.REVOKING:
                    # A legacy/inconsistent row may have lost its durable task. Do not
                    # change generation; reconstruct the task for this current epoch.
                    pass
                else:
                    return {
                        "status": str(configuration.status),
                        "vpn_configuration_id": vpn_configuration_id,
                    }

            await uow.vpn.save_entity(configuration)
            config_row = await uow.vpn.get_configuration_with_endpoint(vpn_configuration_id)
            if config_row is None:
                raise RuntimeError("VPN configuration disappeared while preparing revoke")

            if config_row.get("node_id") is None:
                task = await self._create_panel_task(
                    uow,
                    configuration_id=vpn_configuration_id,
                    subscription_id=subscription_id,
                    generation=configuration.generation,
                    config_row=config_row,
                    now=now,
                )
                if task.id is None:
                    raise RuntimeError("Panel revoke task was not persisted")
                return RevokePlan(
                    vpn_configuration_id=vpn_configuration_id,
                    panel_task_id=task.id,
                    should_enqueue=task.status is PanelRevokeTaskStatus.PENDING,
                )

            task = await self._create_node_task(
                uow,
                config_row=config_row,
                vpn_configuration_id=vpn_configuration_id,
                subscription_id=subscription_id,
                generation=configuration.generation,
                now=now,
            )
            if task.id is None:
                raise RuntimeError("Node revoke task was not persisted")
            return RevokePlan(
                vpn_configuration_id=vpn_configuration_id,
                node_task_id=task.id,
                should_enqueue=task.status is NodeTaskStatus.PENDING,
            )

    async def _existing_current_revoke_task(
        self,
        uow: UnitOfWork,
        configuration: Any,
        config_row: Mapping[str, Any],
    ) -> dict[str, str | int] | None:
        if configuration.id is None:
            return None
        if config_row.get("node_id") is None:
            task = await uow.vpn.get_panel_revoke_task_for_generation(
                configuration.id,
                configuration.generation,
            )
            if task is None or task.id is None:
                return None
            if task.status is PanelRevokeTaskStatus.FAILED:
                # A failed cleanup is not reusable as the owner of a deliberate
                # retry. Force retry must start a new generation/operation epoch.
                return None
            return {
                "status": "queued" if task.status is PanelRevokeTaskStatus.PENDING else str(task.status),
                "vpn_configuration_id": configuration.id,
                "panel_task_id": task.id,
            }
        task = await uow.nodes.get_vpn_task_for_generation(
            configuration.id,
            str(NodeTaskOperation.REVOKE_CLIENT),
            configuration.generation,
        )
        if task is None or task.id is None:
            return None
        return {
            "status": "queued" if task.status is NodeTaskStatus.PENDING else str(task.status),
            "vpn_configuration_id": configuration.id,
            "node_task_id": task.id,
        }

    async def _create_node_task(
        self,
        uow: UnitOfWork,
        *,
        config_row: Mapping[str, Any],
        vpn_configuration_id: int,
        subscription_id: int,
        generation: int,
        now: datetime,
    ) -> NodeTask:
        node_id = config_row.get("node_id")
        if node_id is None:
            raise RuntimeError("Node revoke task requires node endpoint")
        task_uuid = uuid4()
        idempotency_key = (
            f"revoke:vpn_configuration:{vpn_configuration_id}:"
            f"generation:{generation}:task:{task_uuid}"
        )
        return await uow.nodes.add_task_entity(
            NodeTask(
                id=None,
                task_uuid=task_uuid,
                node_id=int(node_id),
                operation=NodeTaskOperation.REVOKE_CLIENT,
                status=NodeTaskStatus.PENDING,
                idempotency_key=idempotency_key,
                payload={
                    "task_id": str(task_uuid),
                    "idempotency_key": idempotency_key,
                    "client_uuid": str(config_row["client_uuid"]),
                    "inbound_id": str(config_row.get("local_inbound_id") or self.node_inbound_id),
                    "metadata": {
                        "vpn_configuration_id": vpn_configuration_id,
                        "vpn_generation": generation,
                    },
                },
                vpn_configuration_id=vpn_configuration_id,
                subscription_id=subscription_id,
                vpn_generation=generation,
                max_attempts=self.node_task_max_attempts,
                next_retry_at=now,
            )
        )

    async def _create_panel_task(
        self,
        uow: UnitOfWork,
        *,
        configuration_id: int,
        subscription_id: int,
        generation: int,
        config_row: Mapping[str, Any],
        now: datetime,
    ) -> PanelRevokeTask:
        task_uuid = uuid4()
        idempotency_key = (
            f"panel-revoke:vpn_configuration:{configuration_id}:"
            f"generation:{generation}:task:{task_uuid}"
        )
        return await uow.vpn.add_panel_revoke_task_entity(
            PanelRevokeTask(
                id=None,
                task_uuid=task_uuid,
                vpn_configuration_id=configuration_id,
                subscription_id=subscription_id,
                vpn_generation=generation,
                status=PanelRevokeTaskStatus.PENDING,
                idempotency_key=idempotency_key,
                payload={
                    "xui_inbound_id": config_row.get("local_inbound_id") if config_row.get("local_inbound_id") not in (None, "") else self.xui_inbound_id,
                    "client_uuid": str(config_row["client_uuid"]),
                },
                max_attempts=self.panel_task_max_attempts,
                next_retry_at=now,
            )
        )
