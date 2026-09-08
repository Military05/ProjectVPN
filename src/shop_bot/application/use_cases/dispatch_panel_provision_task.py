from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Literal
from uuid import UUID, uuid4

from shop_bot.application.job_names import JobName
from shop_bot.application.ports import JobQueue, PanelGateway, UnitOfWorkFactory
from shop_bot.domain.entities.panel_task import PanelProvisionTask, PanelProvisionTaskStatus
from shop_bot.domain.entities.vpn import VpnConfigurationStatus, VpnDesiredState
from shop_bot.domain.repositories.interfaces import UnitOfWork


@dataclass(frozen=True, slots=True)
class PreparedPanelDispatch:
    task_id: int
    lease_token: UUID
    attempt_no: int
    action: Literal["provision", "compensate"]
    payload: dict


@dataclass(slots=True)
class DispatchPanelProvisionTask:
    uow_factory: UnitOfWorkFactory
    panel_gateway: PanelGateway
    job_queue: JobQueue
    retry_base_seconds: int
    lease_seconds: int
    clock: Callable[[], datetime]

    async def execute(self, *, panel_task_id: int) -> dict[str, str | int]:
        prepared = await self._prepare(panel_task_id)
        if isinstance(prepared, dict):
            return prepared
        if prepared.action == "compensate":
            return await self._dispatch_compensation(prepared)

        try:
            await self.panel_gateway.provision(prepared.payload)
        except Exception as exc:
            return await self._finalize_remote_failure(prepared, exc)

        try:
            outcome = await self._finalize_provision_success(prepared)
        except Exception as exc:
            await self._compensate_after_local_failure(prepared, exc)
            raise

        if outcome == "activated":
            await self.job_queue.enqueue(JobName.PUBLISH_OUTBOX)
            return {"status": "succeeded", "panel_task_id": prepared.task_id}
        if outcome == "lease_lost":
            # Ownership was replaced by stale-lease recovery. That recovery marks
            # the durable task compensation_required before any new provision.
            return {"status": "lease_lost", "panel_task_id": prepared.task_id}
        if outcome == "compensate":
            return await self._dispatch_compensation(prepared)
        raise RuntimeError(f"Unexpected panel provisioning outcome: {outcome}")

    async def dispatch_due(self, *, limit: int = 100) -> dict[str, int]:
        async with self.uow_factory() as uow:
            task_ids = await uow.vpn.list_due_panel_task_ids(self.clock(), limit=limit)
        queued = 0
        for task_id in task_ids:
            try:
                await self.job_queue.enqueue(JobName.DISPATCH_PANEL_PROVISION_TASK, task_id)
            except Exception:
                continue
            queued += 1
        return {"queued": queued}

    async def recover_stale(self, *, limit: int = 100) -> dict[str, int]:
        now = self.clock()
        recovered_ids: list[int] = []
        async with self.uow_factory() as uow:
            tasks = await uow.vpn.list_stale_panel_task_entities(now, limit=limit)
            for task in tasks:
                task.recover_expired_lease(
                    now=now,
                    error="panel provision lease expired; compensation required before retry",
                )
                await uow.vpn.save_panel_task_entity(task)
                if task.id is not None:
                    recovered_ids.append(task.id)
        queued = 0
        for task_id in recovered_ids:
            try:
                await self.job_queue.enqueue(JobName.DISPATCH_PANEL_PROVISION_TASK, task_id)
            except Exception:
                continue
            queued += 1
        return {"recovered": len(recovered_ids), "queued": queued}

    async def _prepare(self, panel_task_id: int) -> PreparedPanelDispatch | dict[str, str | int]:
        now = self.clock()
        token = uuid4()
        async with self.uow_factory() as uow:
            task = await uow.vpn.get_panel_task_entity(panel_task_id, for_update=True)
            if task is None:
                return {"status": "missing", "panel_task_id": panel_task_id}
            if task.status in {
                PanelProvisionTaskStatus.SUCCEEDED,
                PanelProvisionTaskStatus.FAILED,
                PanelProvisionTaskStatus.CANCELLED,
            }:
                return {"status": str(task.status), "panel_task_id": panel_task_id}
            if not task.can_dispatch(now):
                return {"status": str(task.status), "panel_task_id": panel_task_id}
            attempt_no = task.start_attempt(
                now=now,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                lease_token=token,
            )
            await uow.vpn.save_panel_task_entity(task)
            return PreparedPanelDispatch(
                task_id=panel_task_id,
                lease_token=token,
                attempt_no=attempt_no,
                action="compensate" if task.compensation_required else "provision",
                payload=dict(task.payload),
            )

    async def _finalize_provision_success(self, prepared: PreparedPanelDispatch) -> str:
        now = self.clock()
        async with self.uow_factory() as uow:
            task = await uow.vpn.get_panel_task_entity(prepared.task_id, for_update=True)
            if task is None:
                raise RuntimeError("Panel provision task disappeared during finalization")
            if not task.owns_active_lease(
                token=prepared.lease_token,
                attempt_no=prepared.attempt_no,
                now=now,
            ):
                if task.owns_lease(token=prepared.lease_token, attempt_no=prepared.attempt_no):
                    task.mark_compensation_required(now=now, error="panel lease expired after remote success")
                    await uow.vpn.save_panel_task_entity(task)
                    return "compensate"
                return "lease_lost"

            # Lock order is Task -> Subscription -> VPN. The pre-lock task payload is
            # only operation ownership; current paid access is authoritative here.
            await uow.subscriptions.get_entity(task.subscription_id, for_update=True)
            period = await uow.subscriptions.get_current_period_entity(task.subscription_id, now)
            configuration = await uow.vpn.get_entity(task.vpn_configuration_id, for_update=True)
            if configuration is None:
                raise RuntimeError("VPN configuration for panel task was not found")

            current_operation = (
                task.vpn_generation == configuration.generation
                and configuration.desired_state is VpnDesiredState.ACTIVE
                and configuration.status is VpnConfigurationStatus.PROVISIONING
            )
            if current_operation and period is not None:
                configuration.activate()
                await uow.vpn.save_entity(configuration)
                task.complete(now=now)
                await uow.vpn.save_panel_task_entity(task)
                await uow.payments.create_outbox_event(
                    event_name="vpn_configuration_activated",
                    aggregate_type="vpn_configuration",
                    aggregate_id=task.vpn_configuration_id,
                    payload={
                        "vpn_configuration_id": task.vpn_configuration_id,
                        "subscription_id": task.subscription_id,
                    },
                )
                return "activated"

            if current_operation and period is None:
                configuration.cancel_provisioning_for_revoke()
                await uow.vpn.save_entity(configuration)
                error = "subscription access expired during panel provisioning"
            else:
                error = "stale panel provision success requires compensation"
            task.mark_compensation_required(now=now, error=error)
            await uow.vpn.save_panel_task_entity(task)
            return "compensate"

    async def _finalize_remote_failure(
        self, prepared: PreparedPanelDispatch, error: Exception
    ) -> dict[str, str | int]:
        now = self.clock()
        async with self.uow_factory() as uow:
            task = await uow.vpn.get_panel_task_entity(prepared.task_id, for_update=True)
            if task is None or not task.owns_lease(
                token=prepared.lease_token, attempt_no=prepared.attempt_no
            ):
                return {"status": "lease_lost", "panel_task_id": prepared.task_id}
            task.retry(
                now=now,
                error=f"panel provision failed: {type(error).__name__}",
                base_delay_seconds=self.retry_base_seconds,
            )
            await uow.vpn.save_panel_task_entity(task)
            if task.status is PanelProvisionTaskStatus.FAILED:
                configuration = await uow.vpn.get_entity(task.vpn_configuration_id, for_update=True)
                if (
                    configuration is not None
                    and task.vpn_generation == configuration.generation
                    and configuration.desired_state is VpnDesiredState.ACTIVE
                    and configuration.status is VpnConfigurationStatus.PROVISIONING
                ):
                    configuration.fail_provisioning()
                    await uow.vpn.save_entity(configuration)
                return {"status": "failed", "panel_task_id": prepared.task_id}
            return {"status": "retry_scheduled", "panel_task_id": prepared.task_id}

    async def _dispatch_compensation(self, prepared: PreparedPanelDispatch) -> dict[str, str | int]:
        try:
            await self.panel_gateway.revoke(prepared.payload)
        except Exception as exc:
            await self._record_compensation_failure(prepared, exc)
            return {"status": "compensation_required", "panel_task_id": prepared.task_id}
        return await self._record_compensation_success(prepared)

    async def _record_compensation_success(self, prepared: PreparedPanelDispatch) -> dict[str, str | int]:
        now = self.clock()
        async with self.uow_factory() as uow:
            task = await uow.vpn.get_panel_task_entity(prepared.task_id, for_update=True)
            if task is None or not task.owns_lease(
                token=prepared.lease_token, attempt_no=prepared.attempt_no
            ):
                return {"status": "lease_lost", "panel_task_id": prepared.task_id}
            configuration = await uow.vpn.get_entity(task.vpn_configuration_id, for_update=True)
            terminal_local_state = (
                configuration is None
                or task.vpn_generation != configuration.generation
                or configuration.desired_state is VpnDesiredState.REVOKED
                or configuration.status in {
                    VpnConfigurationStatus.EXPIRED,
                    VpnConfigurationStatus.REVOKED,
                    VpnConfigurationStatus.DISABLED,
                }
            )
            task.compensation_succeeded(now=now, terminal_local_state=terminal_local_state)
            await uow.vpn.save_panel_task_entity(task)
            if (
                task.status is PanelProvisionTaskStatus.FAILED
                and configuration is not None
                and task.vpn_generation == configuration.generation
                and configuration.desired_state is VpnDesiredState.ACTIVE
                and configuration.status is VpnConfigurationStatus.PROVISIONING
            ):
                configuration.fail_provisioning()
                await uow.vpn.save_entity(configuration)
            return {"status": str(task.status), "panel_task_id": prepared.task_id}

    async def _record_compensation_failure(self, prepared: PreparedPanelDispatch, error: Exception) -> None:
        now = self.clock()
        async with self.uow_factory() as uow:
            task = await uow.vpn.get_panel_task_entity(prepared.task_id, for_update=True)
            if task is None or not task.owns_lease(
                token=prepared.lease_token, attempt_no=prepared.attempt_no
            ):
                return
            task.require_compensation(
                now=now,
                error=f"panel compensation failed: {type(error).__name__}",
            )
            await uow.vpn.save_panel_task_entity(task)

    async def _compensate_after_local_failure(
        self, prepared: PreparedPanelDispatch, original_error: Exception
    ) -> None:
        try:
            await self.panel_gateway.revoke(prepared.payload)
        except Exception as compensation_error:
            try:
                await self._record_compensation_failure(prepared, compensation_error)
            except Exception as persistence_error:
                original_error.add_note(
                    "Failed to persist compensation-required state: "
                    f"{type(persistence_error).__name__}"
                )
            original_error.add_note(
                f"Panel compensation failed: {type(compensation_error).__name__}"
            )
            return
        try:
            await self._record_compensation_success(prepared)
        except Exception as persistence_error:
            original_error.add_note(
                f"Panel compensation succeeded but local recovery persistence failed: {type(persistence_error).__name__}"
            )
