from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings
from arq.worker import run_worker

from shop_bot.application.commands.dispatch_node_task import (
    dispatch_due_node_tasks,
    dispatch_node_task as dispatch_node_task_command,
)
from shop_bot.application.commands.process_payment_event import (
    process_payment_event as process_payment_event_command,
)
from shop_bot.application.commands.provision_vpn_configuration import (
    provision_vpn_configuration as provision_vpn_configuration_command,
)
from shop_bot.application.commands.publish_outbox_events import publish_outbox_events
from shop_bot.application.commands.revoke_vpn_configuration import (
    revoke_vpn_configuration as revoke_vpn_configuration_command,
)
from shop_bot.application.commands.sync_expired_subscriptions import sync_expired_subscriptions
from shop_bot.application.commands.sync_node_status import sync_node_status
from shop_bot.bootstrap.container import build_container
from shop_bot.core.config import get_settings


async def startup(ctx: dict) -> None:
    ctx["container"] = await build_container(
        include_arq_pool=True,
        service_name="shopbot-worker",
    )


async def shutdown(ctx: dict) -> None:
    await ctx["container"].close()


async def process_payment_event(ctx: dict, payment_event_id: int) -> dict:
    return dict(
        await process_payment_event_command(
            ctx["container"],
            payment_event_id=payment_event_id,
        )
    )


async def provision_subscription(ctx: dict, subscription_id: int) -> dict:
    return dict(
        await provision_vpn_configuration_command(
            ctx["container"],
            subscription_id=subscription_id,
        )
    )


async def revoke_vpn_configuration(ctx: dict, vpn_configuration_id: int) -> dict:
    return dict(
        await revoke_vpn_configuration_command(
            ctx["container"],
            vpn_configuration_id=vpn_configuration_id,
        )
    )


async def dispatch_node_task(ctx: dict, node_task_id: int) -> dict:
    return dict(
        await dispatch_node_task_command(
            ctx["container"],
            node_task_id=node_task_id,
        )
    )


async def dispatch_due_node_tasks_job(ctx: dict) -> dict:
    return dict(await dispatch_due_node_tasks(ctx["container"]))


async def sync_node_status_job(ctx: dict, node_id: int | None = None) -> dict:
    return dict(await sync_node_status(ctx["container"], node_id=node_id))


async def sync_expired_subscriptions_job(ctx: dict) -> dict:
    return dict(await sync_expired_subscriptions(ctx["container"]))


async def publish_outbox_job(ctx: dict) -> dict:
    return dict(await publish_outbox_events(ctx["container"]))


class WorkerSettings:
    settings = get_settings()

    functions = [
        process_payment_event,
        provision_subscription,
        revoke_vpn_configuration,
        dispatch_node_task,
        dispatch_due_node_tasks_job,
        sync_node_status_job,
        sync_expired_subscriptions_job,
        publish_outbox_job,
    ]

    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    queue_name = settings.worker_queue_name
    max_jobs = 10

    cron_jobs = [
        cron(
            sync_expired_subscriptions_job,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
        ),
        cron(
            publish_outbox_job,
            minute={1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56},
        ),
        cron(
            sync_node_status_job,
            minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57},
        ),
        cron(
            dispatch_due_node_tasks_job,
            minute=set(range(60)),
        ),
    ]


def main() -> None:
    run_worker(WorkerSettings)