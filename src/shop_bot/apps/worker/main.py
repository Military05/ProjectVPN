from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func, run_worker

from shop_bot.application.commands.dispatch_node_task import dispatch_due_node_tasks, dispatch_node_task
from shop_bot.application.commands.dispatch_panel_provision_task import (
    dispatch_due_panel_provision_tasks,
    dispatch_panel_provision_task,
    recover_stale_panel_provision_tasks,
)
from shop_bot.application.commands.dispatch_panel_revoke_task import (
    dispatch_due_panel_revoke_tasks,
    dispatch_panel_revoke_task,
    recover_stale_panel_revoke_tasks,
)
from shop_bot.application.commands.process_payment_event import process_payment_event
from shop_bot.application.commands.recover_stale_node_tasks import recover_stale_node_tasks
from shop_bot.application.commands.recover_payment_events import recover_payment_events
from shop_bot.application.commands.provision_vpn_configuration import provision_vpn_configuration
from shop_bot.application.commands.publish_outbox_events import publish_outbox_events
from shop_bot.application.commands.revoke_vpn_configuration import revoke_vpn_configuration
from shop_bot.application.commands.sync_expired_subscriptions import sync_expired_subscriptions
from shop_bot.application.commands.sync_node_status import sync_node_status
from shop_bot.application.job_names import JobName
from shop_bot.bootstrap.container import build_container
from shop_bot.core.config import get_settings
from shop_bot.infrastructure.persistence.sqlalchemy.schema import assert_schema_at_head


STARTUP_RECOVERY_LIMIT = 100


async def startup(ctx: dict) -> None:
    ctx["container"] = await build_container(include_arq_pool=True, service_name="shopbot-worker")
    await assert_schema_at_head(ctx["container"].engine)
    ctx["startup_recovery"] = await run_startup_recovery(ctx["container"])


async def run_startup_recovery(
    container: Any, *, limit: int = STARTUP_RECOVERY_LIMIT
) -> dict[str, Mapping[str, Any]]:
    """Recreate bounded Redis delivery from durable PostgreSQL intents."""

    return {
        "payment_events": await recover_payment_events(container, limit=limit),
        "stale_node_tasks": await recover_stale_node_tasks(container, limit=limit),
        "due_node_tasks": await dispatch_due_node_tasks(container, limit=limit),
        "stale_panel_provision_tasks": await recover_stale_panel_provision_tasks(
            container, limit=limit
        ),
        "due_panel_provision_tasks": await dispatch_due_panel_provision_tasks(
            container, limit=limit
        ),
        "stale_panel_revoke_tasks": await recover_stale_panel_revoke_tasks(
            container, limit=limit
        ),
        "due_panel_revoke_tasks": await dispatch_due_panel_revoke_tasks(
            container, limit=limit
        ),
    }


async def shutdown(ctx: dict) -> None:
    await ctx["container"].close()


async def process_payment_event_job(ctx: dict, payment_event_id: int) -> dict:
    return dict(await process_payment_event(ctx["container"], payment_event_id=payment_event_id))


async def provision_subscription_job(ctx: dict, subscription_id: int) -> dict:
    return dict(await provision_vpn_configuration(ctx["container"], subscription_id=subscription_id))


async def revoke_vpn_configuration_job(
    ctx: dict, vpn_configuration_id: int, reason: str = "expiration"
) -> dict:
    return dict(
        await revoke_vpn_configuration(
            ctx["container"],
            vpn_configuration_id=vpn_configuration_id,
            reason=reason,
        )
    )


async def dispatch_node_task_job(ctx: dict, node_task_id: int) -> dict:
    return dict(await dispatch_node_task(ctx["container"], node_task_id=node_task_id))


async def dispatch_panel_provision_task_job(ctx: dict, panel_task_id: int) -> dict:
    return dict(await dispatch_panel_provision_task(ctx["container"], panel_task_id=panel_task_id))


async def dispatch_due_panel_provision_tasks_job(ctx: dict) -> dict:
    return dict(await dispatch_due_panel_provision_tasks(ctx["container"]))


async def recover_stale_panel_provision_tasks_job(ctx: dict) -> dict:
    return dict(await recover_stale_panel_provision_tasks(ctx["container"]))


async def dispatch_panel_revoke_task_job(ctx: dict, panel_revoke_task_id: int) -> dict:
    return dict(
        await dispatch_panel_revoke_task(
            ctx["container"], panel_revoke_task_id=panel_revoke_task_id
        )
    )


async def dispatch_due_panel_revoke_tasks_job(ctx: dict) -> dict:
    return dict(await dispatch_due_panel_revoke_tasks(ctx["container"]))


async def recover_stale_panel_revoke_tasks_job(ctx: dict) -> dict:
    return dict(await recover_stale_panel_revoke_tasks(ctx["container"]))


async def dispatch_due_node_tasks_job(ctx: dict) -> dict:
    return dict(await dispatch_due_node_tasks(ctx["container"]))


async def recover_stale_node_tasks_job(ctx: dict) -> dict:
    return dict(await recover_stale_node_tasks(ctx["container"]))


async def sync_node_status_job(ctx: dict, node_id: int | None = None) -> dict:
    return dict(await sync_node_status(ctx["container"], node_id=node_id))


async def sync_expired_subscriptions_job(ctx: dict) -> dict:
    return dict(await sync_expired_subscriptions(ctx["container"]))


async def publish_outbox_job(ctx: dict) -> dict:
    return dict(await publish_outbox_events(ctx["container"]))


async def recover_payment_events_job(ctx: dict) -> dict:
    return dict(await recover_payment_events(ctx["container"]))


class WorkerSettings:
    settings = get_settings()
    functions = [
        func(process_payment_event_job, name=JobName.PROCESS_PAYMENT_EVENT.value),
        func(provision_subscription_job, name=JobName.PROVISION_SUBSCRIPTION.value),
        func(revoke_vpn_configuration_job, name=JobName.REVOKE_VPN_CONFIGURATION.value),
        func(dispatch_node_task_job, name=JobName.DISPATCH_NODE_TASK.value),
        func(dispatch_panel_provision_task_job, name=JobName.DISPATCH_PANEL_PROVISION_TASK.value),
        func(dispatch_panel_revoke_task_job, name=JobName.DISPATCH_PANEL_REVOKE_TASK.value),
        dispatch_due_node_tasks_job,
        dispatch_due_panel_provision_tasks_job,
        dispatch_due_panel_revoke_tasks_job,
        recover_stale_panel_provision_tasks_job,
        recover_stale_panel_revoke_tasks_job,
        recover_stale_node_tasks_job,
        sync_node_status_job,
        sync_expired_subscriptions_job,
        func(publish_outbox_job, name=JobName.PUBLISH_OUTBOX.value),
        recover_payment_events_job,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    queue_name = settings.worker_queue_name
    max_jobs = 10
    cron_jobs = [
        cron(recover_payment_events_job, minute=set(range(60))),
        cron(sync_expired_subscriptions_job, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(publish_outbox_job, minute={1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56}),
        cron(sync_node_status_job, minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57}),
        cron(dispatch_due_node_tasks_job, minute=set(range(60))),
        cron(recover_stale_node_tasks_job, minute=set(range(60))),
        cron(dispatch_due_panel_provision_tasks_job, minute=set(range(60))),
        cron(recover_stale_panel_provision_tasks_job, minute=set(range(60))),
        cron(dispatch_due_panel_revoke_tasks_job, minute=set(range(60))),
        cron(recover_stale_panel_revoke_tasks_job, minute=set(range(60))),
    ]


def main() -> None:
    run_worker(WorkerSettings)
