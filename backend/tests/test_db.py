from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.config import settings
from app.db import Database


def test_migraties_zijn_herhaalbaar(tmp_path: Path) -> None:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    test_settings = replace(
        settings,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
    )
    database = Database(test_settings)
    database.initialize()
    database.initialize()

    assert database.applied_versions() == {1, 2, 3, 4, 5, 6}
    assert database.fetch_one("PRAGMA journal_mode")[0] == "wal"
    assert database.fetch_one("SELECT COUNT(*) FROM functie")[0] == 12
