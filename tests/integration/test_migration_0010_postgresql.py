from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


ROOT = Path(__file__).resolve().parents[2]
DATABASE_ENV = "PROJECTVPN_MIGRATION_0010_TEST_DATABASE_URL"
DATABASE_URL = os.getenv(DATABASE_ENV)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgresql,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason=f"set {DATABASE_ENV} to a disposable PostgreSQL 15+ database",
    ),
]


def _asyncpg_url(raw_url: str) -> URL:
    url = make_url(raw_url)
    if url.drivername in {"postgres", "postgresql"}:
        return url.set(drivername="postgresql+asyncpg")
    if url.drivername != "postgresql+asyncpg":
        raise ValueError(f"{DATABASE_ENV} must use PostgreSQL/asyncpg")
    return url


def _upgrade(revision: str, database_url: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=ROOT,
        env=environment,
        check=True,
    )


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None) or getattr(
        error.orig, "sql_state", None
    )


@pytest.mark.asyncio
async def test_real_postgresql_0010_rolling_upgrade_contract() -> None:
    assert DATABASE_URL is not None
    database_url = _asyncpg_url(DATABASE_URL).render_as_string(hide_password=False)
    engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")

    try:
        async with engine.connect() as connection:
            version = int(await connection.scalar(text("SHOW server_version_num")))
            assert version >= 150000
            await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))

        _upgrade("20260906_0009", database_url)

        async with engine.connect() as connection:
            before_id = int(
                await connection.scalar(
                    text(
                        """
                        INSERT INTO outbox_events (
                            event_name, aggregate_type, aggregate_id, status, payload
                        ) VALUES (
                            'legacy_before_0010', 'vpn_configuration', 7, 'pending',
                            '{"phase":"before"}'::jsonb
                        )
                        RETURNING outbox_event_id
                        """
                    )
                )
            )
            assert await connection.scalar(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE legacy_outbox_event_id = :legacy_id"
                ),
                {"legacy_id": before_id},
            ) == 0

        _upgrade("20260913_0010", database_url)

        async with engine.connect() as connection:
            catch_up_payload = await connection.scalar(
                text(
                    "SELECT payload FROM audit_events "
                    "WHERE legacy_outbox_event_id = :legacy_id"
                ),
                {"legacy_id": before_id},
            )
            assert catch_up_payload == {"phase": "before"}

            after_id = int(
                await connection.scalar(
                    text(
                        """
                        INSERT INTO outbox_events (
                            event_name, aggregate_type, aggregate_id, status, payload
                        ) VALUES (
                            'legacy_after_0010', 'vpn_configuration', 8, 'pending',
                            '{"phase":"after"}'::jsonb
                        )
                        RETURNING outbox_event_id
                        """
                    )
                )
            )
            mirrored_payload = await connection.scalar(
                text(
                    "SELECT payload FROM audit_events "
                    "WHERE legacy_outbox_event_id = :legacy_id"
                ),
                {"legacy_id": after_id},
            )
            assert mirrored_payload == {"phase": "after"}

            assert await connection.scalar(
                text("SELECT to_regclass('public.outbox_events')")
            ) == "outbox_events"
            assert await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260913_0010"
            index_definitions = list(
                (
                    await connection.execute(
                        text(
                            "SELECT indexdef FROM pg_indexes "
                            "WHERE schemaname = 'public' AND tablename = 'audit_events'"
                        )
                    )
                ).scalars()
            )
            assert any(
                "(aggregate_type, aggregate_id, created_at DESC)" in definition
                for definition in index_definitions
            )
            assert any(
                "(event_name, created_at DESC)" in definition
                for definition in index_definitions
            )

        with pytest.raises(DBAPIError) as update_error:
            async with engine.connect() as connection:
                await connection.execute(
                    text(
                        "UPDATE audit_events SET event_name = 'mutated' "
                        "WHERE legacy_outbox_event_id = :legacy_id"
                    ),
                    {"legacy_id": before_id},
                )
        assert _sqlstate(update_error.value) == "55000"

        with pytest.raises(DBAPIError) as delete_error:
            async with engine.connect() as connection:
                await connection.execute(
                    text(
                        "DELETE FROM audit_events "
                        "WHERE legacy_outbox_event_id = :legacy_id"
                    ),
                    {"legacy_id": before_id},
                )
        assert _sqlstate(delete_error.value) == "55000"

        # A repeated deployment at head is a no-op and must preserve both rows.
        _upgrade("head", database_url)
        async with engine.connect() as connection:
            assert await connection.scalar(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE legacy_outbox_event_id IN (:before_id, :after_id)"
                ),
                {"before_id": before_id, "after_id": after_id},
            ) == 2
    finally:
        async with engine.connect() as connection:
            await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))
        await engine.dispose()
