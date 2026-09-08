from __future__ import annotations

from shop_bot.application.job_names import JobName
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import UUID, uuid4

from shop_bot.application.ports import JobQueue, NodeGateway, UnitOfWorkFactory
from shop_bot.domain.entities.node import NodeTask, NodeTaskOperation, NodeTaskStatus
from shop_bot.domain.entities.vpn import VpnConfigurationStatus, VpnDesiredState
from shop_bot.domain.repositories.interfaces import UnitOfWork


@dataclass(frozen=True, slots=True)
class PreparedDispatch:
    task: NodeTask
    node: Mapping[str, Any]
    credential: Mapping[str, Any]
    attempt_id: int
    attempt_no: int
    lease_token: UUID


@dataclass(frozen=True, slots=True)
class DispatchFinalization:
    status: str
    follow_up_task_id: int | None = None
    publish_outbox: bool = False


@dataclass(slots=True)
class DispatchNodeTask:
    uow_factory: UnitOfWorkFactory
    node_gateway: NodeGateway
    job_queue: JobQueue
    retry_base_seconds: int
    lease_seconds: int
    clock: Callable[[], datetime]

    async def execute(self, *, node_task_id: int) -> dict[str, Any]:
        prepared = await self._prepare(node_task_id=node_task_id, started_at=self.clock())
        if isinstance(prepared, dict):
            return prepared

        response, request_error = await self._send(prepared)
        finalization = await self._finalize(
            prepared=prepared,
            response=response,
            request_error=request_error,
            completed_at=self.clock(),
        )
        if finalization.follow_up_task_id is not None:
            await self.job_queue.enqueue(JobName.DISPATCH_NODE_TASK, finalization.follow_up_task_id)
        if finalization.publish_outbox:
            await self.job_queue.enqueue(JobName.PUBLISH_OUTBOX)
        return {"status": finalization.status, "node_task_id": node_task_id}

    async def dispatch_due(self, *, limit: int = 100) -> dict[str, Any]:
        now = self.clock()
        async with self.uow_factory() as uow:
            task_ids = await uow.nodes.list_due_tasks(now, limit=limit)
        for task_id in task_ids:
            await self.job_queue.enqueue(JobName.DISPATCH_NODE_TASK, task_id)
        return {"status": "queued", "task_count": len(task_ids)}

    async def recover_stale(self, *, limit: int = 100) -> dict[str, Any]:
        now = self.clock()
        requeue_ids: list[int] = []
        recovered = 0
        failed = 0
        async with self.uow_factory() as uow:
            stale_tasks = await uow.nodes.list_stale_task_entities(now, limit=limit)
            for task in stale_tasks:
                if task.id is None or not task.lease_is_expired(now):
                    continue
                attempt_no = task.attempts
                await uow.nodes.fail_started_task_attempt(
                    node_task_id=task.id,
                    attempt_no=attempt_no,
                    finished_at=now,
                    error_message="node task lease expired",
                )
                task.recover_expired_lease(
                    now=now,
                    error="node task lease expired",
                    base_delay_seconds=self.retry_base_seconds,
                )
                await uow.nodes.save_task_entity(task)
                recovered += 1
                if task.status is NodeTaskStatus.FAILED:
                    failed += 1
                    await self._mark_configuration_failure(uow, task)
                elif task.status is NodeTaskStatus.PENDING:
                    requeue_ids.append(task.id)

        enqueue_failures = 0
        for task_id in requeue_ids:
            try:
                await self.job_queue.enqueue(JobName.DISPATCH_NODE_TASK, task_id)
            except Exception:
                # Recovery is already durable. The existing due-task cron is the fallback wakeup.
                enqueue_failures += 1
        return {
            "status": "recovered",
            "recovered": recovered,
            "requeued": len(requeue_ids),
            "failed": failed,
            "enqueue_failures": enqueue_failures,
        }

    async def _prepare(
        self,
        *,
        node_task_id: int,
        started_at: datetime,
    ) -> PreparedDispatch | dict[str, Any]:
        async with self.uow_factory() as uow:
            task = await uow.nodes.get_task_entity(node_task_id, for_update=True)
            early_result = self._early_result(task, node_task_id, started_at)
            if early_result is not None:
                return early_result
            assert task is not None

            lease_token = uuid4()
            lease_expires_at = started_at + timedelta(seconds=self.lease_seconds)
            attempt_no = task.start_attempt(
                started_at,
                lease_expires_at=lease_expires_at,
                lease_token=lease_token,
            )
            await uow.nodes.save_task_entity(task)
            attempt = await uow.nodes.create_task_attempt(
                node_task_id=node_task_id,
                attempt_no=attempt_no,
                request_payload=task.payload,
                started_at=started_at,
            )
            attempt_id = int(attempt["node_task_attempt_id"])
            node = await uow.nodes.get_node(task.node_id)
            if node is None:
                await self._record_preflight_failure(
                    uow,
                    task=task,
                    attempt_id=attempt_id,
                    error="Node not found",
                    at=started_at,
                )
                return {"status": "node_missing", "node_task_id": node_task_id}

            credential = await uow.nodes.get_active_credential(task.node_id)
            if credential is None:
                await self._record_preflight_failure(
                    uow,
                    task=task,
                    attempt_id=attempt_id,
                    error="Active node credential not found",
                    at=started_at,
                )
                return {"status": "credential_missing", "node_task_id": node_task_id}
            return PreparedDispatch(
                task=task,
                node=node,
                credential=credential,
                attempt_id=attempt_id,
                attempt_no=attempt_no,
                lease_token=lease_token,
            )

    @staticmethod
    def _early_result(task: NodeTask | None, node_task_id: int, now: datetime) -> dict[str, Any] | None:
        if task is None:
            return {"status": "missing"}
        if task.status is NodeTaskStatus.SUCCEEDED:
            return {"status": "succeeded", "node_task_id": node_task_id}
        if task.status is NodeTaskStatus.FAILED:
            return {"status": "failed", "node_task_id": node_task_id}
        if not task.can_dispatch(now):
            return {"status": "scheduled", "node_task_id": node_task_id}
        return None

    async def _record_preflight_failure(
        self,
        uow: UnitOfWork,
        *,
        task: NodeTask,
        attempt_id: int,
        error: str,
        at: datetime,
    ) -> None:
        await uow.nodes.finish_task_attempt(
            attempt_id,
            status="failed",
            finished_at=at,
            error_message=error,
        )
        await self._fail_or_retry(
            uow,
            task=task,
            last_error=error,
            response_payload=None,
            completed_at=at,
        )

    async def _send(self, prepared: PreparedDispatch) -> tuple[dict[str, Any] | None, str | None]:
        try:
            if prepared.task.operation is NodeTaskOperation.PROVISION_CLIENT:
                response = await self.node_gateway.provision_client(
                    node=prepared.node,
                    credential=prepared.credential,
                    payload=prepared.task.payload,
                    idempotency_key=prepared.task.idempotency_key,
                )
                return response, None
            if prepared.task.operation is NodeTaskOperation.REVOKE_CLIENT:
                response = await self.node_gateway.revoke_client(
                    node=prepared.node,
                    credential=prepared.credential,
                    payload=prepared.task.payload,
                    idempotency_key=prepared.task.idempotency_key,
                )
                return response, None
            return None, f"Unsupported node task operation: {prepared.task.operation}"
        except Exception as exc:  # pragma: no cover - depends on remote node
            return None, str(exc)

    async def _finalize(
        self,
        *,
        prepared: PreparedDispatch,
        response: dict[str, Any] | None,
        request_error: str | None,
        completed_at: datetime,
    ) -> DispatchFinalization:
        task_id = prepared.task.id
        if task_id is None:
            return DispatchFinalization(status="missing")
        async with self.uow_factory() as uow:
            task = await uow.nodes.get_task_entity(task_id, for_update=True)
            if task is None:
                return DispatchFinalization(status="missing")
            if not task.owns_active_lease(prepared.lease_token, completed_at, prepared.attempt_no):
                return DispatchFinalization(status="stale_attempt")
            if request_error is not None:
                return await self._finalize_transport_failure(
                    uow,
                    task=task,
                    attempt_id=prepared.attempt_id,
                    error=request_error,
                    completed_at=completed_at,
                )

            remote_status = str(response.get("status", "failed")) if response is not None else "failed"
            succeeded = task.accepts_remote_status(remote_status)
            error = None if succeeded else self._error_from_response(response)
            await uow.nodes.finish_task_attempt(
                prepared.attempt_id,
                status="succeeded" if succeeded else "failed",
                finished_at=completed_at,
                response_payload=response,
                error_message=error,
            )
            if not succeeded:
                status = await self._fail_or_retry(
                    uow,
                    task=task,
                    last_error=error or "Node request failed",
                    response_payload=response,
                    completed_at=completed_at,
                )
                return DispatchFinalization(status=status)
            return await self._finalize_success(uow, task=task, response=response or {}, completed_at=completed_at)

    async def _finalize_transport_failure(
        self,
        uow: UnitOfWork,
        *,
        task: NodeTask,
        attempt_id: int,
        error: str,
        completed_at: datetime,
    ) -> DispatchFinalization:
        await uow.nodes.finish_task_attempt(
            attempt_id,
            status="failed",
            finished_at=completed_at,
            error_message=error,
        )
        status = await self._fail_or_retry(
            uow,
            task=task,
            last_error=error,
            response_payload=None,
            completed_at=completed_at,
        )
        return DispatchFinalization(status=status)

    async def _finalize_success(
        self,
        uow: UnitOfWork,
        *,
        task: NodeTask,
        response: dict[str, Any],
        completed_at: datetime,
    ) -> DispatchFinalization:
        remote_status = str(response.get("status", "succeeded"))
        task.complete(response=response, now=completed_at)
        await uow.nodes.save_task_entity(task)
        if task.operation is NodeTaskOperation.PROVISION_CLIENT and task.vpn_configuration_id is not None:
            return await self._complete_provision(uow, task=task, remote_status=remote_status, completed_at=completed_at)
        if task.operation is NodeTaskOperation.REVOKE_CLIENT and task.vpn_configuration_id is not None:
            return await self._complete_revoke(uow, task=task, remote_status=remote_status, completed_at=completed_at)
        return DispatchFinalization(status=remote_status)

    async def _complete_provision(
        self,
        uow: UnitOfWork,
        *,
        task: NodeTask,
        remote_status: str,
        completed_at: datetime,
    ) -> DispatchFinalization:
        # Task ownership is already locked by _finalize. Preserve global ordering:
        # Task -> Subscription -> VPN.
        current_period = None
        if task.subscription_id is not None:
            await uow.subscriptions.get_entity(task.subscription_id, for_update=True)
            current_period = await uow.subscriptions.get_current_period_entity(
                task.subscription_id, completed_at
            )
        configuration = await uow.vpn.get_entity(task.vpn_configuration_id, for_update=True)
        if configuration is None:
            raise RuntimeError("VPN configuration for node task was not found")

        current_operation = (
            task.vpn_generation == configuration.generation
            and configuration.desired_state is VpnDesiredState.ACTIVE
            and configuration.status is VpnConfigurationStatus.PROVISIONING
        )
        if current_operation and current_period is not None:
            configuration.activate(task.remote_client_ref)
            await uow.vpn.save_entity(configuration)
            await uow.payments.create_outbox_event(
                event_name="vpn_configuration_activated",
                aggregate_type="vpn_configuration",
                aggregate_id=task.vpn_configuration_id,
                payload={
                    "vpn_configuration_id": task.vpn_configuration_id,
                    "subscription_id": task.subscription_id,
                },
            )
            return DispatchFinalization(status=remote_status, publish_outbox=True)

        if current_operation and current_period is None:
            configuration.cancel_provisioning_for_revoke()
            configuration.remote_client_ref = task.remote_client_ref or configuration.remote_client_ref
            await uow.vpn.save_entity(configuration)

        # The remote provision side effect happened. Whether access expired or this
        # response is stale, cleanup must be a fresh physical operation identity.
        follow_up = await uow.nodes.add_task_entity(
            self._revoke_follow_up(
                task,
                completed_at,
                vpn_generation=configuration.generation,
            )
        )
        if follow_up.id is None:
            raise RuntimeError("Follow-up node task was not persisted")
        return DispatchFinalization(status=remote_status, follow_up_task_id=follow_up.id)

    async def _complete_revoke(
        self,
        uow: UnitOfWork,
        *,
        task: NodeTask,
        remote_status: str,
        completed_at: datetime,
    ) -> DispatchFinalization:
        configuration = await uow.vpn.get_entity(task.vpn_configuration_id, for_update=True)
        if configuration is None:
            return DispatchFinalization(status=remote_status)
        if (
            task.vpn_generation == configuration.generation
            and configuration.desired_state is VpnDesiredState.REVOKED
            and configuration.status is VpnConfigurationStatus.REVOKING
        ):
            configuration.revoke(completed_at)
            await uow.vpn.save_entity(configuration)
            await uow.payments.create_outbox_event(
                event_name="vpn_configuration_revoked",
                aggregate_type="vpn_configuration",
                aggregate_id=task.vpn_configuration_id,
                payload={"vpn_configuration_id": task.vpn_configuration_id},
            )
            return DispatchFinalization(status=remote_status, publish_outbox=True)
        # Cleanup success or a stale generation is a successful remote outcome but
        # must never rewrite newer local intent or emit duplicate lifecycle events.
        return DispatchFinalization(status=remote_status)

    def _revoke_follow_up(
        self, task: NodeTask, now: datetime, *, vpn_generation: int
    ) -> NodeTask:
        task_uuid = uuid4()
        idempotency_key = (
            f"revoke:vpn_configuration:{task.vpn_configuration_id}:"
            f"generation:{vpn_generation}:task:{task_uuid}"
        )
        return NodeTask(
            id=None,
            task_uuid=task_uuid,
            node_id=task.node_id,
            operation=NodeTaskOperation.REVOKE_CLIENT,
            status=NodeTaskStatus.PENDING,
            idempotency_key=idempotency_key,
            payload=self._build_revoke_payload(
                task.payload,
                task_uuid=task_uuid,
                idempotency_key=idempotency_key,
                vpn_generation=vpn_generation,
            ),
            vpn_configuration_id=task.vpn_configuration_id,
            subscription_id=task.subscription_id,
            vpn_generation=vpn_generation,
            max_attempts=task.max_attempts,
            next_retry_at=now,
        )

    async def _fail_or_retry(
        self,
        uow: UnitOfWork,
        *,
        task: NodeTask,
        last_error: str,
        response_payload: dict[str, Any] | None,
        completed_at: datetime,
    ) -> str:
        task.retry(
            error=last_error,
            response=response_payload,
            now=completed_at,
            base_delay_seconds=self.retry_base_seconds,
        )
        await uow.nodes.save_task_entity(task)
        if task.status is not NodeTaskStatus.FAILED:
            return "retry_scheduled"
        await self._mark_configuration_failure(uow, task)
        return "failed"

    @staticmethod
    async def _mark_configuration_failure(uow: UnitOfWork, task: NodeTask) -> None:
        if task.vpn_configuration_id is None:
            return
        configuration = await uow.vpn.get_entity(task.vpn_configuration_id, for_update=True)
        if configuration is None or task.vpn_generation != configuration.generation:
            return
        if (
            task.operation is NodeTaskOperation.PROVISION_CLIENT
            and configuration.desired_state is VpnDesiredState.ACTIVE
            and configuration.status is VpnConfigurationStatus.PROVISIONING
        ):
            configuration.fail_provisioning()
        elif (
            task.operation is NodeTaskOperation.REVOKE_CLIENT
            and configuration.desired_state is VpnDesiredState.REVOKED
            and configuration.status is VpnConfigurationStatus.REVOKING
        ):
            configuration.fail_revoke()
        else:
            return
        await uow.vpn.save_entity(configuration)

    @staticmethod
    def _error_from_response(response: dict[str, Any] | None) -> str:
        if response is None:
            return "Node response was empty"
        if response.get("message"):
            return str(response["message"])
        if response.get("error_code"):
            return str(response["error_code"])
        if response.get("status"):
            return f"Node returned status={response['status']}"
        return "Node request failed"

    @staticmethod
    def _build_revoke_payload(
        payload: dict[str, Any],
        *,
        task_uuid: UUID,
        idempotency_key: str,
        vpn_generation: int,
    ) -> dict[str, Any]:
        metadata = dict(payload.get("metadata") or {})
        metadata["vpn_generation"] = vpn_generation
        metadata["compensation_for_task_id"] = str(payload.get("task_id", ""))
        return {
            "task_id": str(task_uuid),
            "idempotency_key": idempotency_key,
            "client_uuid": str(payload["client_uuid"]),
            "inbound_id": str(payload["inbound_id"]),
            "metadata": metadata,
        }
