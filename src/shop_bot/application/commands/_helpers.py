from __future__ import annotations

from typing import Any

from shop_bot.bootstrap.container import ServiceContainer


async def enqueue_job(container: ServiceContainer, job_name: str, *args: Any) -> None:
    if container.arq_redis is None:
        return
    await container.arq_redis.enqueue_job(job_name, *args, _queue_name=container.settings.worker_queue_name)
