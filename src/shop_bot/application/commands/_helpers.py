from __future__ import annotations

from typing import Any

from shop_bot.application.job_names import JobName


async def enqueue_job(container: Any, job_name: JobName, *args: Any) -> None:
    """Compatibility helper routed through the injected queue adapter."""
    await container.job_queue.enqueue(job_name, *args)
