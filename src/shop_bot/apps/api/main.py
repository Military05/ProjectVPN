from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse, ORJSONResponse

from shop_bot.apps.api.routes import (
    admin,
    admin_ui,
    bot_commands,
    bot_queries,
    health,
    nodes,
    sandbox,
    subscriptions,
    webhooks,
)
from shop_bot.bootstrap.container import build_container
from shop_bot.bootstrap.seed_demo import seed_demo_data
from shop_bot.core.exceptions import ConflictError, NotFoundError, ValidationError
from shop_bot.core.observability import instrument_fastapi


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = await build_container(include_arq_pool=True, service_name="shopbot-api")
    app.state.container = container
    if container.settings.auto_seed_demo_data:
        await seed_demo_data()
    yield
    await container.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="vless-shopbot API",
        version="3.0.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )
    instrument_fastapi(app)

    app.include_router(health.router)
    app.include_router(bot_queries.router)
    app.include_router(bot_commands.router)
    app.include_router(admin.router)
    app.include_router(nodes.router)
    app.include_router(webhooks.router)
    app.include_router(subscriptions.router)
    app.include_router(sandbox.router)
    admin_ui.register_admin_ui(app)

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request, exc: NotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request, exc: ValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ConflictError)
    async def conflict_error_handler(request, exc: ConflictError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    return app