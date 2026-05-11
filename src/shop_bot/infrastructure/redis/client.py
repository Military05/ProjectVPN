from __future__ import annotations

from redis.asyncio import Redis

from shop_bot.core.config import Settings


_redis: Redis | None = None


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)


def set_redis(redis: Redis) -> None:
    global _redis
    _redis = redis


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis has not been initialized")
    return _redis
