from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from shop_bot.application.ports import JobQueue, PanelGateway, PaymentGatewayRegistry
from shop_bot.application.queries import BotQueryService, NodeQueryService
from shop_bot.application.use_cases import (
    ActivateSubscription,
    AdminOperations,
    CreatePayment,
    DispatchNodeTask,
    DispatchPanelProvisionTask,
    DispatchPanelRevokeTask,
    IngestWebhook,
    NodeAdministration,
    ProcessPayment,
    ProvisionVpn,
    PublishOutbox,
    RegisterBotUser,
    RevokeVpn,
    SyncExpiredSubscriptions,
    SyncNodeStatus,
)
from shop_bot.core.config import Settings, get_settings
from shop_bot.core.time import utcnow
from shop_bot.domain.services import SubscriptionPolicy, VpnProvisioningService
from shop_bot.domain.vpn.builder import VlessUriBuilder
from shop_bot.infrastructure.nodes.client import NodeApiClient
from shop_bot.infrastructure.persistence.sqlalchemy.engine import create_engine, set_engine
from shop_bot.infrastructure.persistence.sqlalchemy.uow import SqlAlchemyUnitOfWork


@dataclass(slots=True)
class ApplicationServices:
    register_user: RegisterBotUser
    admin: AdminOperations
    node_admin: NodeAdministration
    create_payment: CreatePayment
    ingest_webhook: IngestWebhook
    process_payment: ProcessPayment
    provision_vpn: ProvisionVpn
    revoke_vpn: RevokeVpn
    dispatch_node_task: DispatchNodeTask
    dispatch_panel_provision_task: DispatchPanelProvisionTask
    dispatch_panel_revoke_task: DispatchPanelRevokeTask
    sync_expired_subscriptions: SyncExpiredSubscriptions
    sync_node_status: SyncNodeStatus
    publish_outbox: PublishOutbox


@dataclass(slots=True)
class ApplicationQueries:
    bot: BotQueryService
    nodes: NodeQueryService


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    engine: AsyncEngine
    redis: Any
    deduplicator: Any
    payment_registry: PaymentGatewayRegistry
    panel_adapter: PanelGateway
    applications: ApplicationServices
    queries: ApplicationQueries
    job_queue: JobQueue
    arq_redis: Any | None = None

    def uow(self) -> SqlAlchemyUnitOfWork:
        """Compatibility factory for integration code outside application services."""
        return SqlAlchemyUnitOfWork(self.engine)

    async def close(self) -> None:
        await self.redis.aclose()
        if self.arq_redis is not None:
            await self.arq_redis.close()
        await self.engine.dispose()


def _build_application_services(
    *,
    settings: Settings,
    engine: AsyncEngine,
    payment_registry: PaymentGatewayRegistry,
    panel_adapter: PanelGateway,
    job_queue: JobQueue,
) -> tuple[ApplicationServices, ApplicationQueries]:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    node_gateway = NodeApiClient(settings)
    activate_subscription = ActivateSubscription(SubscriptionPolicy())
    sync_expired_subscriptions = SyncExpiredSubscriptions(
        uow_factory=uow_factory,
        job_queue=job_queue,
        clock=utcnow,
    )
    sync_node_status = SyncNodeStatus(
        uow_factory=uow_factory,
        node_gateway=node_gateway,
        clock=utcnow,
        concurrency=settings.node_status_sync_concurrency,
        batch_size=settings.node_status_sync_batch_size,
        probe_lease_seconds=settings.node_status_probe_lease_seconds,
        stale_after_seconds=settings.node_health_stale_after_seconds,
    )

    applications = ApplicationServices(
        register_user=RegisterBotUser(uow_factory),
        admin=AdminOperations(
            uow_factory=uow_factory,
            job_queue=job_queue,
            reconcile_subscriptions=sync_expired_subscriptions,
            activate_subscription=activate_subscription,
            clock=utcnow,
        ),
        node_admin=NodeAdministration(
            uow_factory=uow_factory,
            job_queue=job_queue,
            sync_status=sync_node_status,
        ),
        create_payment=CreatePayment(
            uow_factory=uow_factory,
            payment_registry=payment_registry,
            job_queue=job_queue,
            default_provider=settings.payment_default_provider,
            return_url=str(settings.payment_return_url),
            creation_lease_seconds=settings.payment_invoice_creation_lease_seconds,
            clock=utcnow,
        ),
        ingest_webhook=IngestWebhook(
            uow_factory=uow_factory,
            payment_registry=payment_registry,
            job_queue=job_queue,
            default_currency=settings.default_currency,
            clock=utcnow,
        ),
        process_payment=ProcessPayment(
            uow_factory=uow_factory,
            activate_subscription=activate_subscription,
            job_queue=job_queue,
            clock=utcnow,
        ),
        provision_vpn=ProvisionVpn(
            uow_factory=uow_factory,
            provisioning_service=VpnProvisioningService(VlessUriBuilder()),
            job_queue=job_queue,
            display_name_prefix=settings.default_display_name_prefix,
            node_inbound_id=settings.node_agent_inbound_id,
            node_task_max_attempts=settings.node_task_max_attempts,
            panel_task_max_attempts=settings.panel_task_max_attempts,
            xui_inbound_id=settings.xui_inbound_id,
            clock=utcnow,
            node_health_stale_after_seconds=settings.node_health_stale_after_seconds,
        ),
        revoke_vpn=RevokeVpn(
            uow_factory=uow_factory,
            job_queue=job_queue,
            node_inbound_id=settings.node_agent_inbound_id,
            node_task_max_attempts=settings.node_task_max_attempts,
            panel_task_max_attempts=settings.panel_task_max_attempts,
            xui_inbound_id=settings.xui_inbound_id,
            clock=utcnow,
        ),
        dispatch_node_task=DispatchNodeTask(
            uow_factory=uow_factory,
            node_gateway=node_gateway,
            job_queue=job_queue,
            retry_base_seconds=settings.node_task_retry_base_seconds,
            lease_seconds=settings.node_task_lease_seconds,
            clock=utcnow,
        ),
        dispatch_panel_provision_task=DispatchPanelProvisionTask(
            uow_factory=uow_factory,
            panel_gateway=panel_adapter,
            job_queue=job_queue,
            retry_base_seconds=settings.panel_task_retry_base_seconds,
            lease_seconds=settings.panel_task_lease_seconds,
            clock=utcnow,
        ),
        dispatch_panel_revoke_task=DispatchPanelRevokeTask(
            uow_factory=uow_factory,
            panel_gateway=panel_adapter,
            job_queue=job_queue,
            retry_base_seconds=settings.panel_task_retry_base_seconds,
            lease_seconds=settings.panel_task_lease_seconds,
            clock=utcnow,
        ),
        sync_expired_subscriptions=sync_expired_subscriptions,
        sync_node_status=sync_node_status,
        publish_outbox=PublishOutbox(uow_factory=uow_factory, clock=utcnow),
    )
    queries = ApplicationQueries(
        bot=BotQueryService(uow_factory=uow_factory, builder=VlessUriBuilder(), clock=utcnow),
        nodes=NodeQueryService(uow_factory=uow_factory),
    )
    return applications, queries


async def build_container(
    *,
    include_arq_pool: bool = False,
    service_name: str = "shopbot",
    settings: Settings | None = None,
) -> ServiceContainer:
    """Build concrete runtime adapters and inject them into application services."""

    from arq.connections import RedisSettings, create_pool

    from shop_bot.core.logging import configure_logging
    from shop_bot.core.observability import setup_sentry, setup_tracing
    from shop_bot.infrastructure.messaging import ArqJobQueue
    from shop_bot.infrastructure.panel.adapter import build_panel_adapter
    from shop_bot.infrastructure.payments.registry import PaymentAdapterRegistry
    from shop_bot.infrastructure.redis.client import create_redis
    from shop_bot.infrastructure.redis.dedup import RedisDeduplicator

    settings = settings or get_settings()
    configure_logging(settings)
    setup_sentry(settings)
    setup_tracing(settings, service_name=service_name)

    engine = create_engine(settings)
    set_engine(engine)
    redis = create_redis(settings)

    arq_redis = None
    if include_arq_pool:
        arq_redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    payment_registry = PaymentAdapterRegistry(settings)
    panel_adapter = build_panel_adapter(settings)
    job_queue = ArqJobQueue(arq_redis, queue_name=settings.worker_queue_name)
    applications, queries = _build_application_services(
        settings=settings,
        engine=engine,
        payment_registry=payment_registry,
        panel_adapter=panel_adapter,
        job_queue=job_queue,
    )
    return ServiceContainer(
        settings=settings,
        engine=engine,
        redis=redis,
        deduplicator=RedisDeduplicator(redis),
        payment_registry=payment_registry,
        panel_adapter=panel_adapter,
        applications=applications,
        queries=queries,
        job_queue=job_queue,
        arq_redis=arq_redis,
    )
