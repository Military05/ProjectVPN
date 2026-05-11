from __future__ import annotations

from dataclasses import dataclass

from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from shop_bot.core.config import Settings, get_settings
from shop_bot.core.logging import configure_logging
from shop_bot.core.observability import setup_sentry, setup_tracing
from shop_bot.domain.subscriptions.service import SubscriptionService
from shop_bot.domain.vpn.builder import VlessUriBuilder
from shop_bot.domain.vpn.service import VpnService
from shop_bot.infrastructure.db.engine import create_engine, set_engine
from shop_bot.infrastructure.db.uow import SqlAlchemyUnitOfWork
from shop_bot.infrastructure.panel.adapter import PanelAdapter, build_panel_adapter
from shop_bot.infrastructure.payments.registry import PaymentAdapterRegistry
from shop_bot.infrastructure.redis.client import create_redis, set_redis
from shop_bot.infrastructure.redis.dedup import RedisDeduplicator


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    engine: AsyncEngine
    redis: Redis
    deduplicator: RedisDeduplicator
    payment_registry: PaymentAdapterRegistry
    panel_adapter: PanelAdapter
    subscription_service: SubscriptionService
    vpn_service: VpnService
    arq_redis: ArqRedis | None = None

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)

    async def close(self) -> None:
        await self.redis.aclose()
        if self.arq_redis is not None:
            await self.arq_redis.close()
        await self.engine.dispose()


async def build_container(*, include_arq_pool: bool = False, service_name: str = "shopbot") -> ServiceContainer:
    settings = get_settings()
    configure_logging(settings)
    setup_sentry(settings)
    setup_tracing(settings, service_name=service_name)

    engine = create_engine(settings)
    set_engine(engine)
    redis = create_redis(settings)
    set_redis(redis)

    arq_redis = None
    if include_arq_pool:
        arq_redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    return ServiceContainer(
        settings=settings,
        engine=engine,
        redis=redis,
        deduplicator=RedisDeduplicator(redis),
        payment_registry=PaymentAdapterRegistry(settings),
        panel_adapter=build_panel_adapter(settings),
        subscription_service=SubscriptionService(),
        vpn_service=VpnService(VlessUriBuilder()),
        arq_redis=arq_redis,
    )
