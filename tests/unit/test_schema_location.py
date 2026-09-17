from pathlib import Path

from shop_bot.infrastructure.persistence.sqlalchemy import schema


def test_migration_root_uses_runtime_working_directory_for_installed_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (tmp_path / "alembic").mkdir()
    installed_module = (
        tmp_path
        / "python"
        / "site-packages"
        / "shop_bot"
        / "infrastructure"
        / "persistence"
        / "sqlalchemy"
        / "schema.py"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(schema, "__file__", str(installed_module))

    assert schema._migration_root() == tmp_path
