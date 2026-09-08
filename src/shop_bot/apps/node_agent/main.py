from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from shop_bot.apps.node_agent.routes import router
from shop_bot.apps.node_agent.runtime import build_runtime
from shop_bot.core.config import get_settings
from shop_bot.core.logging import configure_logging
from shop_bot.core.observability import instrument_fastapi, setup_sentry, setup_tracing
from shop_bot.core.time import utcnow
from shop_bot.infrastructure.nodes.auth import InMemoryNonceStore
from shop_bot.infrastructure.nodes.idempotency import NodeOperationJournal


async def _journal_maintenance(journal: NodeOperationJournal, retention_days: int) -> None:
    while True:
        await asyncio.sleep(24 * 60 * 60)
        await journal.prune_completed(utcnow() - timedelta(days=retention_days))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    setup_sentry(settings)
    setup_tracing(settings, service_name="shopbot-node-agent")
    app.state.settings = settings
    app.state.runtime = build_runtime(settings)
    app.state.nonce_store = InMemoryNonceStore(settings.node_timestamp_tolerance_seconds)
    journal = NodeOperationJournal(
        settings.node_agent_idempotency_db_path,
        lease_seconds=settings.node_agent_operation_lease_seconds,
    )
    await journal.initialize()
    await journal.prune_completed(utcnow() - timedelta(days=settings.node_agent_idempotency_retention_days))
    maintenance_task = asyncio.create_task(
        _journal_maintenance(journal, settings.node_agent_idempotency_retention_days)
    )
    app.state.operation_journal = journal
    try:
        yield
    finally:
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
        await journal.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="vless-shopbot node agent",
        version="1.0.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )
    instrument_fastapi(app)
    app.include_router(router)
    return app
