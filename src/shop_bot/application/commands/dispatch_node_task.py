from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from shop_bot.application.commands._helpers import enqueue_job
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.time import utcnow
from shop_bot.infrastructure.nodes.client import NodeApiClient

PROVISION_SUCCESS_STATUSES = {"provisioned", "already_exists"}
REVOKE_SUCCESS_STATUSES = {"revoked", "not_found_treated_as_success"}


async def dispatch_node_task(container: ServiceContainer, *, node_task_id: int) -> Mapping[str, Any]:
    started_at = utcnow()
    follow_up_task_id: int | None = None
    publish_outbox = False
    result = "unknown"

    async with container.uow() as uow:
        task = await uow.nodes.get_task(node_task_id, for_update=True)
        if task is None:
            return {"status": "missing"}
        if task["status"] == "succeeded":
            return {"status": "succeeded", "node_task_id": node_task_id}
        if task["status"] == "failed":
            return {"status": "failed", "node_task_id": node_task_id}
        if task["status"] == "pending" and task["next_retry_at"] > started_at:
            return {"status": "scheduled", "node_task_id": node_task_id}

        in_progress = await uow.nodes.mark_task_in_progress(node_task_id, started_at=started_at)
        if in_progress is None:
            return {"status": "missing", "node_task_id": node_task_id}
        attempt = await uow.nodes.create_task_attempt(
            node_task_id=node_task_id,
            attempt_no=int(in_progress["attempts"]),
            request_payload=dict(in_progress["payload"] or {}),
            started_at=started_at,
        )

        node = await uow.nodes.get_node(int(in_progress["node_id"]))
        if node is None:
            await uow.nodes.finish_task_attempt(
                int(attempt["node_task_attempt_id"]),
                status="failed",
                finished_at=started_at,
                error_message="Node not found",
            )
            await _fail_or_retry(
                container,
                uow,
                task=in_progress,
                node_task_id=node_task_id,
                last_error="Node not found",
                response_payload=None,
                completed_at=started_at,
            )
            return {"status": "node_missing", "node_task_id": node_task_id}

        credential = await uow.nodes.get_active_credential(int(in_progress["node_id"]))
        if credential is None:
            await uow.nodes.finish_task_attempt(
                int(attempt["node_task_attempt_id"]),
                status="failed",
                finished_at=started_at,
                error_message="Active node credential not found",
            )
            await _fail_or_retry(
                container,
                uow,
                task=in_progress,
                node_task_id=node_task_id,
                last_error="Active node credential not found",
                response_payload=None,
                completed_at=started_at,
            )
            return {"status": "credential_missing", "node_task_id": node_task_id}

    client = NodeApiClient(container.settings)
    task_response: dict[str, Any] | None = None
    request_error: str | None = None
    try:
        if in_progress["operation"] == "provision_client":
            task_response = await client.provision_client(
                node=node,
                credential=credential,
                payload=dict(in_progress["payload"] or {}),
                idempotency_key=str(in_progress["idempotency_key"]),
            )
        elif in_progress["operation"] == "revoke_client":
            task_response = await client.revoke_client(
                node=node,
                credential=credential,
                payload=dict(in_progress["payload"] or {}),
                idempotency_key=str(in_progress["idempotency_key"]),
            )
        else:
            request_error = f"Unsupported node task operation: {in_progress['operation']}"
    except Exception as exc:  # pragma: no cover - network/path dependent
        request_error = str(exc)

    completed_at = utcnow()
    async with container.uow() as uow:
        task = await uow.nodes.get_task(node_task_id, for_update=True)
        if task is None:
            return {"status": "missing", "node_task_id": node_task_id}

        if request_error is not None:
            await uow.nodes.finish_task_attempt(
                int(attempt["node_task_attempt_id"]),
                status="failed",
                finished_at=completed_at,
                error_message=request_error,
            )
            await uow.nodes.mark_node_unreachable(int(task["node_id"]), error_message=request_error, seen_at=completed_at)
            result = await _fail_or_retry(
                container,
                uow,
                task=task,
                node_task_id=node_task_id,
                last_error=request_error,
                response_payload=None,
                completed_at=completed_at,
            )
            return {"status": result, "node_task_id": node_task_id}

        status = str(task_response.get("status", "failed")) if task_response is not None else "failed"
        is_success = _is_success(operation=str(task["operation"]), status=status)
        await uow.nodes.finish_task_attempt(
            int(attempt["node_task_attempt_id"]),
            status="succeeded" if is_success else "failed",
            finished_at=completed_at,
            response_payload=task_response,
            error_message=None if is_success else _error_from_response(task_response),
        )

        if is_success:
            remote_client_ref = task_response.get("remote_client_ref") if task_response is not None else None
            await uow.nodes.mark_task_succeeded(
                node_task_id,
                response_payload=task_response or {},
                remote_client_ref=remote_client_ref,
                completed_at=completed_at,
            )
            await uow.nodes.touch_node_success(int(task["node_id"]), seen_at=completed_at)
            if task["operation"] == "provision_client" and task["vpn_configuration_id"] is not None:
                vpn_configuration_id = int(task["vpn_configuration_id"])
                current_period = None
                if task["subscription_id"] is not None:
                    current_period = await uow.subscriptions.get_current_period(int(task["subscription_id"]), completed_at)
                if current_period is None:
                    await uow.vpn.mark_configuration_status(
                        vpn_configuration_id,
                        status="expired",
                        remote_client_ref=remote_client_ref,
                    )
                    follow_up_task = await uow.nodes.create_task(
                        node_id=int(task["node_id"]),
                        operation="revoke_client",
                        idempotency_key=f"revoke:vpn_configuration:{vpn_configuration_id}",
                        payload=_build_revoke_payload(dict(task["payload"] or {})),
                        vpn_configuration_id=vpn_configuration_id,
                        subscription_id=int(task["subscription_id"]) if task["subscription_id"] is not None else None,
                        max_attempts=int(task["max_attempts"]),
                    )
                    follow_up_task_id = int(follow_up_task["node_task_id"])
                else:
                    await uow.vpn.activate_configuration(
                        vpn_configuration_id,
                        remote_client_ref=remote_client_ref,
                    )
                    await uow.payments.create_outbox_event(
                        event_name="vpn_configuration_activated",
                        aggregate_type="vpn_configuration",
                        aggregate_id=vpn_configuration_id,
                        payload={
                            "vpn_configuration_id": vpn_configuration_id,
                            "subscription_id": int(task["subscription_id"]) if task["subscription_id"] is not None else None,
                        },
                    )
                    publish_outbox = True
            elif task["operation"] == "revoke_client" and task["vpn_configuration_id"] is not None:
                vpn_configuration_id = int(task["vpn_configuration_id"])
                await uow.vpn.revoke_configuration(vpn_configuration_id, revoked_at=completed_at, status="revoked")
                await uow.payments.create_outbox_event(
                    event_name="vpn_configuration_revoked",
                    aggregate_type="vpn_configuration",
                    aggregate_id=vpn_configuration_id,
                    payload={"vpn_configuration_id": vpn_configuration_id},
                )
                publish_outbox = True
            result = status
        else:
            failure_message = _error_from_response(task_response)
            result = await _fail_or_retry(
                container,
                uow,
                task=task,
                node_task_id=node_task_id,
                last_error=failure_message,
                response_payload=task_response,
                completed_at=completed_at,
            )

    if follow_up_task_id is not None:
        await enqueue_job(container, "dispatch_node_task", follow_up_task_id)
    if publish_outbox:
        await enqueue_job(container, "publish_outbox")
    return {"status": result, "node_task_id": node_task_id}


async def dispatch_due_node_tasks(container: ServiceContainer) -> Mapping[str, Any]:
    now = utcnow()
    async with container.uow() as uow:
        task_ids = await uow.nodes.list_due_tasks(now, limit=100)
    for task_id in task_ids:
        await enqueue_job(container, "dispatch_node_task", task_id)
    return {"status": "queued", "task_count": len(task_ids)}


async def _fail_or_retry(
    container: ServiceContainer,
    uow,
    *,
    task: Mapping[str, Any],
    node_task_id: int,
    last_error: str,
    response_payload: dict[str, Any] | None,
    completed_at,
) -> str:
    attempts = int(task["attempts"])
    max_attempts = int(task["max_attempts"])
    if attempts >= max_attempts:
        await uow.nodes.mark_task_failed(
            node_task_id,
            last_error=last_error,
            response_payload=response_payload,
            completed_at=completed_at,
        )
        if task["operation"] == "provision_client" and task["vpn_configuration_id"] is not None:
            await uow.vpn.mark_configuration_status(int(task["vpn_configuration_id"]), status="failed")
        elif task["operation"] == "revoke_client" and task["vpn_configuration_id"] is not None:
            await uow.vpn.mark_configuration_status(int(task["vpn_configuration_id"]), status="revoke_failed")
        return "failed"
    delay_seconds = min(container.settings.node_task_retry_base_seconds * (2 ** max(attempts - 1, 0)), 300)
    next_retry_at = completed_at + timedelta(seconds=delay_seconds)
    await uow.nodes.mark_task_retry(
        node_task_id,
        next_retry_at=next_retry_at,
        last_error=last_error,
        response_payload=response_payload,
    )
    return "retry_scheduled"


def _is_success(*, operation: str, status: str) -> bool:
    if operation == "provision_client":
        return status in PROVISION_SUCCESS_STATUSES
    if operation == "revoke_client":
        return status in REVOKE_SUCCESS_STATUSES
    return False


def _error_from_response(response: Mapping[str, Any] | None) -> str:
    if response is None:
        return "Node response was empty"
    if response.get("message"):
        return str(response["message"])
    if response.get("error_code"):
        return str(response["error_code"])
    if response.get("status"):
        return f"Node returned status={response['status']}"
    return "Node request failed"


def _build_revoke_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(payload["task_id"]),
        "idempotency_key": str(payload["idempotency_key"]).replace("provision:", "revoke:"),
        "client_uuid": str(payload["client_uuid"]),
        "inbound_id": str(payload["inbound_id"]),
        "metadata": dict(payload.get("metadata") or {}),
    }
