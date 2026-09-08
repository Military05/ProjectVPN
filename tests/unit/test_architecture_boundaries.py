from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "shop_bot"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_has_no_outward_dependencies() -> None:
    forbidden_prefixes = (
        "shop_bot.application",
        "shop_bot.apps",
        "shop_bot.bootstrap",
        "shop_bot.infrastructure",
        "fastapi",
        "aiogram",
        "sqlalchemy",
        "redis",
        "arq",
    )
    violations: list[str] = []
    for path in (SRC / "domain").rglob("*.py"):
        for imported in _imports(path):
            if imported.startswith(forbidden_prefixes):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert violations == []


def test_use_cases_do_not_depend_on_presentation_or_infrastructure() -> None:
    forbidden_prefixes = ("shop_bot.apps", "shop_bot.bootstrap", "shop_bot.infrastructure")
    violations: list[str] = []
    for path in (SRC / "application" / "use_cases").rglob("*.py"):
        for imported in _imports(path):
            if imported.startswith(forbidden_prefixes):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert violations == []


def test_application_core_has_no_bootstrap_or_infrastructure_dependencies() -> None:
    forbidden_prefixes = ("shop_bot.apps", "shop_bot.bootstrap", "shop_bot.infrastructure")
    violations: list[str] = []
    for directory in ("use_cases", "queries", "commands"):
        for path in (SRC / "application" / directory).rglob("*.py"):
            for imported in _imports(path):
                if imported.startswith(forbidden_prefixes):
                    violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert violations == []


def test_internal_import_graph_has_no_cycles() -> None:
    modules: dict[str, Path] = {}
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(ROOT / "src").with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        modules[".".join(parts)] = path

    graph: dict[str, set[str]] = {module: set() for module in modules}
    for module, path in modules.items():
        for imported in _imports(path):
            if imported in modules:
                graph[module].add(imported)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str, trail: tuple[str, ...]) -> None:
        if module in visiting:
            raise AssertionError("Circular import: " + " -> ".join((*trail, module)))
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph[module]:
            visit(dependency, (*trail, module))
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module, ())


def test_project_functions_are_fully_annotated() -> None:
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            missing = [
                parameter.arg
                for parameter in parameters
                if parameter.arg not in {"self", "cls"} and parameter.annotation is None
            ]
            if node.args.vararg is not None and node.args.vararg.annotation is None:
                missing.append(f"*{node.args.vararg.arg}")
            if node.args.kwarg is not None and node.args.kwarg.annotation is None:
                missing.append(f"**{node.args.kwarg.arg}")
            if missing or node.returns is None:
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} "
                    f"missing={missing}, return_annotation={node.returns is not None}"
                )
    assert violations == []


def test_application_enqueue_calls_do_not_use_literal_job_names() -> None:
    violations: list[str] = []
    for path in (SRC / "application").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "enqueue" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{first.value}")
    assert violations == []


def test_production_code_does_not_import_compatibility_db_facade() -> None:
    facade_prefix = "shop_bot.infrastructure.db"
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        for imported in _imports(path):
            if imported == facade_prefix or imported.startswith(f"{facade_prefix}."):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert violations == []


def test_redis_client_has_no_global_service_locator() -> None:
    path = SRC / "infrastructure" / "redis" / "client.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert function_names.isdisjoint({"get_redis", "set_redis"})
