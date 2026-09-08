from __future__ import annotations

from typing import Any


class RedisDeduplicator:
    def __init__(self, redis: Any) -> None:
        self.redis = redis

    async def ensure_once(self, key: str, ttl_seconds: int) -> bool:
        return bool(await self.redis.set(name=key, value="1", ex=ttl_seconds, nx=True))
