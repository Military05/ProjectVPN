from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


def _heads() -> set[str]:
    root = Path(__file__).resolve().parents[5]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


async def assert_schema_at_head(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        versions = {str(row[0]) for row in result.fetchall()}
    heads = _heads()
    if versions != heads:
        raise RuntimeError(f"Database schema mismatch: current={sorted(versions)} expected={sorted(heads)}")
