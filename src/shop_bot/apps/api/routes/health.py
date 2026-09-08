from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from fastapi import HTTPException, status
from shop_bot.infrastructure.persistence.sqlalchemy.schema import assert_schema_at_head

from shop_bot.apps.api.deps import get_container
from shop_bot.bootstrap.container import ServiceContainer

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(container: ServiceContainer = Depends(get_container)) -> dict[str, str]:
    try:
        async with container.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await container.redis.ping()
        await assert_schema_at_head(container.engine)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="service not ready") from exc
    return {"status": "ready"}
