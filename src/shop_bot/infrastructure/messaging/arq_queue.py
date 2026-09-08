from __future__ import annotations

from typing import Any

from shop_bot.application.job_names import JobName


class ArqJobQueue:
    """ARQ adapter used by application use cases.

    ARQ is imported by the bootstrap only when a runtime container is built. This
    keeps route discovery and static tooling independent from a running queue.
    """

    def __init__(self, arq_redis: Any | None, *, queue_name: str) -> None:
        self._arq_redis = arq_redis
        self._queue_name = queue_name

    async def enqueue(self, job_name: JobName | str, *args: Any) -> None:
        if self._arq_redis is None:
            return
        semantic_name = job_name.value if isinstance(job_name, JobName) else job_name
        await self._arq_redis.enqueue_job(semantic_name, *args, _queue_name=self._queue_name)
