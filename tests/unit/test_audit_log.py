from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from shop_bot.application.commands.publish_outbox_events import publish_outbox_events
from shop_bot.domain.audit import AuditAggregateType, AuditEventName
from shop_bot.infrastructure.persistence.repositories.audit import AuditRepository


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


class ScalarResult:
    def scalar_one(self) -> int:
        return 42


class RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> ScalarResult:
        self.statements.append(statement)
        return ScalarResult()


@pytest.mark.asyncio
async def test_audit_repository_exposes_append_only_insert() -> None:
    connection = RecordingConnection()
    repository = AuditRepository(connection)  # type: ignore[arg-type]
    payload = {"subscription_id": 7}

    event_id = await repository.append(
        event_name=AuditEventName.SUBSCRIPTION_ACTIVATED,
        aggregate_type=AuditAggregateType.SUBSCRIPTION,
        aggregate_id=7,
        payload=payload,
        created_at=NOW,
    )

    assert event_id == 42
    assert len(connection.statements) == 1
    statement = connection.statements[0]
    assert statement.table.name == "audit_events"
    assert statement.compile().params == {
        "event_name": "subscription_activated",
        "aggregate_type": "subscription",
        "aggregate_id": 7,
        "payload": {"subscription_id": 7},
        "created_at": NOW,
    }
    assert payload == {"subscription_id": 7}
    assert {
        name
        for name, member in inspect.getmembers(AuditRepository, inspect.isfunction)
        if not name.startswith("_")
    } == {"append"}


class PoisonContainer:
    def __getattribute__(self, name: str) -> Any:
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError(f"tombstone accessed container.{name}")


@pytest.mark.asyncio
async def test_legacy_publish_job_is_an_exact_database_free_tombstone() -> None:
    assert await publish_outbox_events(PoisonContainer()) == {
        "status": "deprecated_noop"
    }


def test_active_application_has_no_fake_outbox_state_machine() -> None:
    paths = [
        ROOT / "src" / "shop_bot" / "application" / "use_cases",
        ROOT / "src" / "shop_bot" / "bootstrap" / "container.py",
        ROOT / "src" / "shop_bot" / "domain" / "repositories" / "interfaces.py",
        ROOT
        / "src"
        / "shop_bot"
        / "infrastructure"
        / "persistence"
        / "repositories"
        / "payments.py",
    ]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in paths
        for path in ([root] if root.is_file() else sorted(root.glob("*.py")))
    )
    for forbidden in (
        "PublishOutbox",
        "PUBLISH_OUTBOX",
        "create_outbox_event",
        "list_pending_outbox_events",
        "mark_outbox_published",
        "reschedule_outbox_event",
    ):
        assert forbidden not in source


def test_rolling_migration_installs_mirror_before_catch_up_and_enforces_immutability() -> None:
    migration_path = (
        ROOT / "alembic" / "versions" / "20260913_0010_audit_log_runtime.py"
    )
    migration = migration_path.read_text(encoding="utf-8")
    trigger_position = migration.index(
        "CREATE TRIGGER trg_outbox_events_audit_mirror"
    )
    catch_up_position = migration.rindex("INSERT INTO audit_events")

    assert 'down_revision = "20260906_0009"' in migration
    assert trigger_position < catch_up_position
    assert "AFTER INSERT ON outbox_events" in migration
    assert "ON CONFLICT (legacy_outbox_event_id) DO NOTHING" in migration
    assert "BEFORE UPDATE OR DELETE ON audit_events" in migration
    assert "ERRCODE = '55000'" in migration
    assert "aggregate_type, aggregate_id, created_at DESC" in migration
    assert "event_name, created_at DESC" in migration
    assert "drop_table(\"outbox_events\")" not in migration.lower()


def test_worker_keeps_tombstone_registered_but_never_schedules_it() -> None:
    worker_source = (
        ROOT / "src" / "shop_bot" / "apps" / "worker" / "main.py"
    ).read_text(encoding="utf-8")

    assert 'LEGACY_PUBLISH_OUTBOX_JOB_NAME = "publish_outbox"' in worker_source
    assert "func(publish_outbox_job, name=LEGACY_PUBLISH_OUTBOX_JOB_NAME)" in worker_source
    cron_section = worker_source.split("cron_jobs = [", maxsplit=1)[1]
    assert "publish_outbox" not in cron_section
