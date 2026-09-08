from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable

import pytest

from shop_bot.application.job_names import JobName
from shop_bot.infrastructure.messaging.arq_queue import ArqJobQueue


@dataclass
class FunctionStub:
    coroutine: Callable[..., Any]
    name: str


def _install_arq_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        import arq  # noqa: F401
    except ModuleNotFoundError:
        arq_is_available = False
    else:
        arq_is_available = True
    if arq_is_available:
        return

    arq_module = ModuleType("arq")
    connections = ModuleType("arq.connections")
    worker = ModuleType("arq.worker")

    def cron(coroutine: Callable[..., Any], **kwargs: Any) -> FunctionStub:
        del kwargs
        return FunctionStub(coroutine=coroutine, name=coroutine.__qualname__)

    class RedisSettingsStub:
        @classmethod
        def from_dsn(cls, dsn: str) -> "RedisSettingsStub":
            del dsn
            return cls()

    def func(coroutine: Callable[..., Any], *, name: str | None = None, **kwargs: Any) -> FunctionStub:
        del kwargs
        return FunctionStub(coroutine=coroutine, name=name or coroutine.__qualname__)

    def run_worker(settings: Any) -> None:
        del settings

    arq_module.cron = cron  # type: ignore[attr-defined]
    connections.RedisSettings = RedisSettingsStub  # type: ignore[attr-defined]
    worker.func = func  # type: ignore[attr-defined]
    worker.run_worker = run_worker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "arq", arq_module)
    monkeypatch.setitem(sys.modules, "arq.connections", connections)
    monkeypatch.setitem(sys.modules, "arq.worker", worker)


def test_all_application_job_names_are_registered_by_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_arq_stub(monkeypatch)
    sys.modules.pop("shop_bot.apps.worker.main", None)
    worker_main = importlib.import_module("shop_bot.apps.worker.main")
    registered = {
        getattr(function, "name", None)
        or getattr(function, "__qualname__", None)
        or getattr(function, "__name__", None)
        for function in worker_main.WorkerSettings.functions
    }
    assert {job.value for job in JobName} <= registered


class RedisSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], str]] = []

    async def enqueue_job(self, name: str, *args: Any, _queue_name: str) -> None:
        self.calls.append((name, args, _queue_name))


@pytest.mark.asyncio
async def test_arq_queue_serializes_job_enum_to_semantic_value() -> None:
    redis = RedisSpy()
    queue = ArqJobQueue(redis, queue_name="shopbot")
    await queue.enqueue(JobName.PROCESS_PAYMENT_EVENT, 123)
    assert redis.calls == [("process_payment_event", (123,), "shopbot")]
