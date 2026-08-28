from __future__ import annotations

import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings, settings
from app.db import Database
from app.onderhoud import (
    record_failure,
    rotate_logs,
    storage_guard,
    weekly_database_maintenance,
)


def _case(tmp_path: Path) -> tuple[Database, Settings, int]:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    configured = replace(
        settings,
        library_root=library,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
    )
    database = Database(configured)
    database.initialize()
    profile_id = database.execute_insert("INSERT INTO profiel(naam,inst) VALUES ('P','{}')")
    library_id = database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id) VALUES ('L',?,?)",
        (str(library), profile_id),
    )
    file_id = database.execute_insert(
        """
        INSERT INTO bestand(library_id,pad,grootte,mtime,categorie,gescand_op)
        VALUES (?,?,1,1,'sdr',CURRENT_TIMESTAMP)
        """,
        (library_id, str(library / "film.mkv")),
    )
    decision_id = database.execute_insert(
        """
        INSERT INTO besluit(bestand_id,actie,baan,reden,payload,gemaakt_op)
        VALUES (?,'hercoderen','cpu','test','{}',CURRENT_TIMESTAMP)
        """,
        (file_id,),
    )
    job_id = database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','klaar')",
        (decision_id, file_id),
    )
    return database, configured, job_id


def test_logrotatie_begrenst_bestand_en_database(tmp_path: Path) -> None:
    database, configured, job_id = _case(tmp_path)
    log_dir = configured.config_root / "logs"
    log_dir.mkdir()
    completed = log_dir / f"job-{job_id}.log"
    completed.write_text("".join(f"regel {index}\n" for index in range(300)))
    old = log_dir / "job-999.log"
    old.write_text("oud\n")
    old_time = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    os.utime(old, (old_time, old_time))
    with database.connection() as connection:
        connection.executemany(
            "INSERT INTO logregel(job_id,niveau,tekst,tijd) VALUES (?, 'info', ?, ?)",
            [(job_id, f"regel {index}", datetime.now(UTC).isoformat()) for index in range(600)],
        )
        connection.commit()

    result = rotate_logs(database, configured)

    assert result == {"verwijderd": 1, "ingekort": 1}
    assert len(completed.read_text().splitlines()) == 200
    count = database.fetch_one("SELECT COUNT(*) AS aantal FROM logregel WHERE job_id=?", (job_id,))
    assert count is not None and count["aantal"] == 500


def test_cachegrenzen_pauzeren_en_herstellen_alleen_eigen_pauze(tmp_path: Path) -> None:
    database, configured, _job_id = _case(tmp_path)
    database.execute(
        "UPDATE instelling SET cache_waarschuwing_gb=1000000000,cache_pauze_gb=999999999"
    )
    assert "gepauzeerd" in str(storage_guard(database, configured)["status"])
    assert all(
        row["gepauzeerd"] for row in database.fetch_all("SELECT gepauzeerd FROM worker_config")
    )

    database.execute("UPDATE instelling SET cache_waarschuwing_gb=1,cache_pauze_gb=1")
    storage_guard(database, configured)
    assert not any(
        row["gepauzeerd"] for row in database.fetch_all("SELECT gepauzeerd FROM worker_config")
    )


def test_weekonderhoud_en_drie_identieke_fouten(tmp_path: Path) -> None:
    database, _configured, job_id = _case(tmp_path)
    now = datetime.now(UTC)
    assert weekly_database_maintenance(database, now)
    assert not weekly_database_maintenance(database, now + timedelta(days=6))
    assert weekly_database_maintenance(database, now + timedelta(days=8))

    assert record_failure(database, job_id, "zelfde fout", "ExecutionError") == 1
    assert record_failure(database, job_id, "zelfde fout", "ExecutionError") == 2
    assert record_failure(database, job_id, "zelfde fout", "ExecutionError") == 3


def test_wachtrijindex_blijft_snel_bij_honderdduizend_jobs(tmp_path: Path) -> None:
    database, _configured, _job_id = _case(tmp_path)
    with database.connection() as connection:
        connection.executescript(
            """
            WITH RECURSIVE seq(x) AS (
                SELECT 2 UNION ALL SELECT x + 1 FROM seq WHERE x <= 100000
            )
            INSERT INTO bestand(id,library_id,pad,grootte,mtime,categorie,gescand_op)
            SELECT x,1,'/library/test-' || x || '.mkv',1000,1,'sdr',CURRENT_TIMESTAMP FROM seq;

            INSERT INTO besluit(id,bestand_id,actie,baan,reden,payload,gemaakt_op)
            SELECT id,id,'hercoderen','cpu','schaaltest','{}',CURRENT_TIMESTAMP
            FROM bestand WHERE id > 1;

            INSERT INTO job(id,besluit_id,bestand_id,baan,status,prioriteit)
            SELECT id,id,id,'cpu','wacht',id % 10 FROM bestand WHERE id > 1;
            """
        )
        started = time.monotonic()
        rows = connection.execute(
            """
            SELECT id FROM job WHERE status='wacht' AND baan='cpu'
            ORDER BY prioriteit DESC,id LIMIT 100
            """
        ).fetchall()
        elapsed = time.monotonic() - started
        plan = " ".join(
            str(row[3])
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM job "
                "WHERE status='wacht' AND baan='cpu' ORDER BY prioriteit DESC,id LIMIT 100"
            )
        )
    assert len(rows) == 100
    assert elapsed < 1.0
    assert "idx_job_status" in plan
