from __future__ import annotations

from redis.asyncio import Redis


class RedisDeduplicator:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def ensure_once(self, key: str, ttl_seconds: int) -> bool:
        return bool(await self.redis.set(name=key, value="1", ex=ttl_seconds, nx=True))
