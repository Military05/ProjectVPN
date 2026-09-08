from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shop_bot.core.config import Settings


_engine: AsyncEngine | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        future=True,
        pool_pre_ping=True,
        echo=settings.debug,
    )


def set_engine(engine: AsyncEngine) -> None:
    global _engine
    _engine = engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine has not been initialized")
    return _engine
