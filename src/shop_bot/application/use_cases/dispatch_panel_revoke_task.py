from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from uuid import UUID, uuid4

from shop_bot.application.job_names import JobName
from shop_bot.application.ports import JobQueue, PanelGateway, UnitOfWorkFactory
from shop_bot.domain.entities.panel_task import PanelRevokeTaskStatus
from shop_bot.domain.entities.vpn import VpnConfigurationStatus, VpnDesiredState


@dataclass(frozen=True, slots=True)
class PreparedPanelRevoke:
    task_id: int
    lease_token: UUID
    attempt_no: int
    payload: dict


@dataclass(slots=True)
class DispatchPanelRevokeTask:
    uow_factory: UnitOfWorkFactory
    panel_gateway: PanelGateway
    job_queue: JobQueue
    retry_base_seconds: int
    lease_seconds: int
    clock: Callable[[], datetime]

    async def execute(self, *, panel_revoke_task_id: int) -> dict[str, str | int]:
        prepared = await self._prepare(panel_revoke_task_id)
        if isinstance(prepared, dict):
            return prepared
        try:
            await self.panel_gateway.revoke(prepared.payload)
        except Exception as exc:
            return await self._finalize_failure(prepared, exc)
        status, publish = await self._finalize_success(prepared)
        if publish:
            await self.job_queue.enqueue(JobName.PUBLISH_OUTBOX)
        return {"status": status, "panel_revoke_task_id": prepared.task_id}

    async def dispatch_due(self, *, limit: int = 100) -> dict[str, int]:
        async with self.uow_factory() as uow:
            task_ids = await uow.vpn.list_due_panel_revoke_task_ids(self.clock(), limit=limit)
        queued = 0
        for task_id in task_ids:
            try:
                await self.job_queue.enqueue(JobName.DISPATCH_PANEL_REVOKE_TASK, task_id)
            except Exception:
                continue
            queued += 1
        return {"queued": queued}

    async def recover_stale(self, *, limit: int = 100) -> dict[str, int]:
        now = self.clock()
        recovered_ids: list[int] = []
        async with self.uow_factory() as uow:
            tasks = await uow.vpn.list_stale_panel_revoke_task_entities(now, limit=limit)
            for task in tasks:
                task.recover_expired_lease(now=now, error="panel revoke lease expired")
                await uow.vpn.save_panel_revoke_task_entity(task)
                if task.id is not None:
                    recovered_ids.append(task.id)
        queued = 0
        for task_id in recovered_ids:
            try:
                await self.job_queue.enqueue(JobName.DISPATCH_PANEL_REVOKE_TASK, task_id)
            except Exception:
                continue
            queued += 1
        return {"recovered": len(recovered_ids), "queued": queued}

    async def _prepare(self, task_id: int) -> PreparedPanelRevoke | dict[str, str | int]:
        now = self.clock()
        token = uuid4()
        async with self.uow_factory() as uow:
            task = await uow.vpn.get_panel_revoke_task_entity(task_id, for_update=True)
            if task is None:
                return {"status": "missing", "panel_revoke_task_id": task_id}
            if task.status in {
                PanelRevokeTaskStatus.SUCCEEDED,
                PanelRevokeTaskStatus.FAILED,
                PanelRevokeTaskStatus.CANCELLED,
            }:
                return {"status": str(task.status), "panel_revoke_task_id": task_id}
            if not task.can_dispatch(now):
                return {"status": str(task.status), "panel_revoke_task_id": task_id}
            attempt_no = task.start_attempt(
                now=now,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                lease_token=token,
            )
            await uow.vpn.save_panel_revoke_task_entity(task)
            return PreparedPanelRevoke(
                task_id=task_id,
                lease_token=token,
                attempt_no=attempt_no,
                payload=dict(task.payload),
            )

    async def _finalize_success(self, prepared: PreparedPanelRevoke) -> tuple[str, bool]:
        now = self.clock()
        async with self.uow_factory() as uow:
            task = await uow.vpn.get_panel_revoke_task_entity(prepared.task_id, for_update=True)
            if task is None:
                return "missing", False
            if not task.owns_active_lease(
                token=prepared.lease_token,
                attempt_no=prepared.attempt_no,
                now=now,
            ):
                return "stale_attempt", False
            configuration = await uow.vpn.get_entity(task.vpn_configuration_id, for_update=True)
            task.complete(now=now)
            await uow.vpn.save_panel_revoke_task_entity(task)
            if configuration is None:
                return "succeeded", False
            if (
                task.vpn_generation == configuration.generation
                and configuration.desired_state is VpnDesiredState.REVOKED
                and configuration.status is VpnConfigurationStatus.REVOKING
            ):
                configuration.revoke(now)
                await uow.vpn.save_entity(configuration)
                await uow.payments.create_outbox_event(
                    event_name="vpn_configuration_revoked",
                    aggregate_type="vpn_configuration",
                    aggregate_id=task.vpn_configuration_id,
                    payload={"vpn_configuration_id": task.vpn_configuration_id},
                )
                return "succeeded", True
            return "succeeded", False

    async def _finalize_failure(
        self, prepared: PreparedPanelRevoke, error: Exception
    ) -> dict[str, str | int]:
        now = self.clock()
        async with self.uow_factory() as uow:
            task = await uow.vpn.get_panel_revoke_task_entity(prepared.task_id, for_update=True)
            if task is None or not task.owns_lease(
                token=prepared.lease_token,
                attempt_no=prepared.attempt_no,
            ):
                return {"status": "stale_attempt", "panel_revoke_task_id": prepared.task_id}
            task.retry(
                now=now,
                error=f"panel revoke failed: {type(error).__name__}",
                base_delay_seconds=self.retry_base_seconds,
            )
            await uow.vpn.save_panel_revoke_task_entity(task)
            if task.status is PanelRevokeTaskStatus.FAILED:
                configuration = await uow.vpn.get_entity(task.vpn_configuration_id, for_update=True)
                if (
                    configuration is not None
                    and task.vpn_generation == configuration.generation
                    and configuration.desired_state is VpnDesiredState.REVOKED
                    and configuration.status is VpnConfigurationStatus.REVOKING
                ):
                    configuration.fail_revoke()
                    await uow.vpn.save_entity(configuration)
                return {"status": "failed", "panel_revoke_task_id": prepared.task_id}
            return {"status": "retry_scheduled", "panel_revoke_task_id": prepared.task_id}
