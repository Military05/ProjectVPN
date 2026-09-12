from __future__ import annotations

from importlib.metadata import version as installed_version
from pathlib import Path
import re
import tomllib

from fastapi.testclient import TestClient

from shop_bot.apps.api.main import create_app


ROOT = Path(__file__).resolve().parents[2]
REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^]]+\])?==(?P<version>[^\s\\]+)\s+\\$"
)
HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}(?:\s+\\)?$")


def _normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_lock(relative_path: str) -> dict[str, str]:
    lines = (ROOT / relative_path).read_text(encoding="utf-8").splitlines()
    starts: list[tuple[int, re.Match[str]]] = []

    for index, line in enumerate(lines):
        if not line or line.startswith(("#", " ")):
            continue
        match = REQUIREMENT_RE.fullmatch(line)
        assert match is not None, f"unpinned requirement in {relative_path}: {line}"
        starts.append((index, match))

    assert starts, f"{relative_path} contains no locked requirements"
    result: dict[str, str] = {}
    for position, (start, match) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        hashes = [line.strip() for line in lines[start + 1 : end] if "--hash=" in line]
        assert hashes, f"missing SHA-256 hash for {match.group('name')} in {relative_path}"
        assert all(HASH_RE.fullmatch(line) for line in hashes)
        result[_normalized_name(match.group("name"))] = match.group("version")

    return result


def test_all_lock_files_use_exact_versions_and_sha256_hashes() -> None:
    for relative_path in (
        "requirements/runtime.lock",
        "requirements/dev.lock",
        "requirements/build.lock",
    ):
        _read_lock(relative_path)


def test_dev_lock_extends_the_exact_runtime_graph() -> None:
    runtime = _read_lock("requirements/runtime.lock")
    dev = _read_lock("requirements/dev.lock")

    assert {name: dev.get(name) for name in runtime} == runtime
    assert {
        "pip": "25.2",
        "pip-tools": "7.5.1",
        "pytest": "8.4.2",
        "pytest-asyncio": "0.26.0",
        "testcontainers": "4.13.3",
    }.items() <= dev.items()


def test_build_lock_matches_pyproject_build_requirements() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    build_requirements = {
        _normalized_name(requirement.split("==", maxsplit=1)[0]): requirement.split(
            "==", maxsplit=1
        )[1]
        for requirement in pyproject["build-system"]["requires"]
    }

    assert _read_lock("requirements/build.lock") == build_requirements


def test_locked_fastapi_prometheus_pair_handles_instrumented_routes() -> None:
    runtime = _read_lock("requirements/runtime.lock")
    assert runtime["fastapi"] == "0.136.3"
    assert runtime["prometheus-fastapi-instrumentator"] == "7.1.0"
    assert installed_version("fastapi") == runtime["fastapi"]
    assert installed_version("prometheus-fastapi-instrumentator") == runtime[
        "prometheus-fastapi-instrumentator"
    ]

    app = create_app()
    assert "_IncludedRouter" not in {type(route).__name__ for route in app.routes}
    response = TestClient(app, raise_server_exceptions=False).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dockerfile_installs_only_hashed_dependency_locks() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim@sha256:" in dockerfile
    assert "--require-hashes -r requirements/build.lock" in dockerfile
    assert "--require-hashes -r requirements/runtime.lock" in dockerfile
    assert "--no-cache-dir --no-deps --no-build-isolation ." in dockerfile
    assert "python -m pip check" in dockerfile
    assert "--upgrade pip" not in dockerfile
