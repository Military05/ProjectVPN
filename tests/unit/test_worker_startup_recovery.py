from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import pytest

from shop_bot.apps.worker import main as worker_main


RECOVERY_FUNCTIONS = (
    "recover_payment_events",
    "recover_stale_node_tasks",
    "dispatch_due_node_tasks",
    "recover_stale_panel_provision_tasks",
    "dispatch_due_panel_provision_tasks",
    "recover_stale_panel_revoke_tasks",
    "dispatch_due_panel_revoke_tasks",
)


@pytest.mark.asyncio
async def test_startup_recovery_runs_every_durable_queue_repair_with_one_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = object()
    calls: list[tuple[str, Any, int]] = []

    def replacement(
        name: str,
    ) -> Callable[..., Coroutine[Any, Any, dict[str, int]]]:
        async def run(received_container: Any, *, limit: int) -> dict[str, int]:
            calls.append((name, received_container, limit))
            return {"handled": 1}

        return run

    for function_name in RECOVERY_FUNCTIONS:
        monkeypatch.setattr(worker_main, function_name, replacement(function_name))

    result = await worker_main.run_startup_recovery(container, limit=17)

    assert calls == [(name, container, 17) for name in RECOVERY_FUNCTIONS]
    assert set(result) == {
        "payment_events",
        "stale_node_tasks",
        "due_node_tasks",
        "stale_panel_provision_tasks",
        "due_panel_provision_tasks",
        "stale_panel_revoke_tasks",
        "due_panel_revoke_tasks",
    }


@pytest.mark.asyncio
async def test_worker_checks_schema_before_startup_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    container = type("Container", (), {"engine": object()})()

    async def build_container(**kwargs: Any) -> Any:
        assert kwargs == {"include_arq_pool": True, "service_name": "shopbot-worker"}
        trace.append("container")
        return container

    async def assert_schema_at_head(engine: Any) -> None:
        assert engine is container.engine
        trace.append("schema")

    async def run_startup_recovery(received_container: Any) -> dict[str, Any]:
        assert received_container is container
        trace.append("recovery")
        return {"status": "completed"}

    monkeypatch.setattr(worker_main, "build_container", build_container)
    monkeypatch.setattr(worker_main, "assert_schema_at_head", assert_schema_at_head)
    monkeypatch.setattr(worker_main, "run_startup_recovery", run_startup_recovery)

    context: dict[str, Any] = {}
    await worker_main.startup(context)

    assert trace == ["container", "schema", "recovery"]
    assert context == {
        "container": container,
        "startup_recovery": {"status": "completed"},
    }
