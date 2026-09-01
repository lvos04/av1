from __future__ import annotations

import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

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

    assert database.applied_versions() == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}
    assert database.fetch_one("PRAGMA journal_mode")[0] == "wal"
    assert database.fetch_one("SELECT COUNT(*) FROM functie")[0] == 13
    assert database.fetch_one(
        "SELECT max_gelijktijdige_encodes FROM instelling WHERE id=1"
    )[0] == 1


def test_migratie_negen_normaliseert_dubbele_wachtrij_en_cpu_workers(tmp_path: Path) -> None:
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
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    source = Path(__file__).parents[1] / "app" / "migrations"
    for migration in sorted(source.glob("00[1-8]_*.sql")):
        shutil.copy2(migration, migrations / migration.name)
    database = Database(test_settings, migrations)
    database.initialize()
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES ('Test','{}')"
    )
    library_id = database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id) VALUES ('Test','/media',?)",
        (profile_id,),
    )

    def add_file(path: str) -> tuple[int, int, int]:
        file_id = database.execute_insert(
            "INSERT INTO bestand(library_id,pad,grootte,mtime,categorie,gescand_op) "
            "VALUES (?,?,1,1,'ok',CURRENT_TIMESTAMP)",
            (library_id, path),
        )
        decision_ids = [
            database.execute_insert(
                "INSERT INTO besluit(bestand_id,actie,baan,reden,payload,status,gemaakt_op) "
                "VALUES (?,'hercoderen','cpu','test','{}','ingepland',CURRENT_TIMESTAMP)",
                (file_id,),
            )
            for _ in range(2)
        ]
        return file_id, decision_ids[0], decision_ids[1]

    completed_file, completed_decision, duplicate_decision = add_file("/media/klaar.mkv")
    database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','klaar')",
        (completed_decision, completed_file),
    )
    completed_duplicate = database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
        (duplicate_decision, completed_file),
    )
    active_file, first_decision, second_decision = add_file("/media/wacht.mkv")
    first = database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
        (first_decision, active_file),
    )
    second = database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','bezig')",
        (second_decision, active_file),
    )
    database.execute("UPDATE worker_config SET aantal=2 WHERE baan='cpu'")
    shutil.copy2(source / "009_queue_safety.sql", migrations / "009_queue_safety.sql")

    database.initialize()

    assert database.fetch_one("SELECT status FROM job WHERE id=?", (completed_duplicate,))[0] == (
        "overgeslagen"
    )
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (first,))[0] == "wacht"
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (second,))[0] == "overgeslagen"
    assert database.fetch_one("SELECT aantal FROM worker_config WHERE baan='cpu'")[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        database.execute_insert(
            "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
            (second_decision, active_file),
        )
