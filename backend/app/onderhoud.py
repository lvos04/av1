"""Begrensde achtergrondtaken voor wekenlang draaien zonder stille vervuiling."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.backups import create_backup, enforce_retention
from app.config import Settings
from app.db import Database

MAX_DB_LOG_LINES = 500
MAX_SUCCESS_LOG_LINES = 200


def append_job_log(database: Database, job_id: int, level: str, text: str) -> None:
    database.execute(
        "INSERT INTO logregel(job_id,niveau,tekst,tijd) VALUES (?,?,?,?)",
        (job_id, level, text[-4000:], datetime.now(UTC).isoformat()),
    )
    database.execute(
        """
        DELETE FROM logregel WHERE job_id=? AND id NOT IN (
            SELECT id FROM logregel WHERE job_id=? ORDER BY id DESC LIMIT ?
        )
        """,
        (job_id, job_id, MAX_DB_LOG_LINES),
    )


def record_failure(database: Database, job_id: int, message: str, error_type: str) -> int:
    signature = hashlib.sha256(f"{error_type}\0{message}".encode()).hexdigest()
    row = database.fetch_one(
        "SELECT laatste_fout_handtekening,zelfde_fout_pogingen FROM job WHERE id=?",
        (job_id,),
    )
    same = (
        int(row["zelfde_fout_pogingen"]) + 1
        if row is not None and row["laatste_fout_handtekening"] == signature
        else 1
    )
    database.execute(
        """
        UPDATE job SET status='mislukt',fout=?,foutsoort=?,klaar_op=?,proces_pid=NULL,
                       laatste_fout_handtekening=?,zelfde_fout_pogingen=?
        WHERE id=?
        """,
        (
            message,
            error_type,
            datetime.now(UTC).isoformat(),
            signature,
            same,
            job_id,
        ),
    )
    append_job_log(database, job_id, "error", message)
    return same


def _trim_file(path: Path, maximum: int) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    if len(lines) > maximum:
        path.write_text("".join(lines[-maximum:]), encoding="utf-8")


def rotate_logs(
    database: Database, settings: Settings, now: datetime | None = None
) -> dict[str, int]:
    current = now or datetime.now(UTC)
    log_dir = settings.config_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    removed = 0
    trimmed = 0
    successful = {
        int(row["id"]) for row in database.fetch_all("SELECT id FROM job WHERE status='klaar'")
    }
    cutoff = current - timedelta(days=30)
    for path in log_dir.glob("job-*.log"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if modified < cutoff:
            path.unlink()
            removed += 1
            continue
        try:
            job_id = int(path.stem.removeprefix("job-"))
        except ValueError:
            continue
        before = path.stat().st_size
        if job_id in successful:
            _trim_file(path, MAX_SUCCESS_LOG_LINES)
            trimmed += int(path.stat().st_size < before)
    database.execute(
        """
        DELETE FROM logregel WHERE id IN (
            SELECT oud.id FROM logregel AS oud
            WHERE (
                SELECT COUNT(*) FROM logregel AS nieuwer
                WHERE nieuwer.job_id=oud.job_id AND nieuwer.id>=oud.id
            ) > ?
        )
        """,
        (MAX_DB_LOG_LINES,),
    )
    return {"verwijderd": removed, "ingekort": trimmed}


def storage_guard(database: Database, settings: Settings) -> dict[str, int | str]:
    free = shutil.disk_usage(settings.cache_root).free
    row = database.fetch_one(
        "SELECT cache_waarschuwing_gb,cache_pauze_gb FROM instelling WHERE id=1"
    )
    warning = int(row["cache_waarschuwing_gb"]) * 1024**3 if row else 50 * 1024**3
    pause = int(row["cache_pauze_gb"]) * 1024**3 if row else 10 * 1024**3
    status = "voldoende"
    if free < pause:
        status = "automatisch gepauzeerd: cache onder pauzegrens"
        database.execute(
            """
            UPDATE worker_config SET gepauzeerd=1,pauze_reden=?
            WHERE gepauzeerd=0 OR pauze_reden LIKE 'automatisch: cache%'
            """,
            (f"automatisch: cache heeft nog {free / 1024**3:.1f} GB vrij",),
        )
    elif free < warning:
        status = "waarschuwing: cache onder waarschuwingsgrens"
        database.execute(
            """
            UPDATE worker_config SET gepauzeerd=0,pauze_reden=NULL
            WHERE pauze_reden LIKE 'automatisch: cache%'
            """
        )
    else:
        database.execute(
            """
            UPDATE worker_config SET gepauzeerd=0,pauze_reden=NULL
            WHERE pauze_reden LIKE 'automatisch: cache%'
            """
        )
    return {"vrij_bytes": free, "status": status}


def weekly_database_maintenance(database: Database, now: datetime | None = None) -> bool:
    current = now or datetime.now(UTC)
    row = database.fetch_one(
        "SELECT laatst_op FROM onderhoud_status WHERE sleutel='sqlite_weekonderhoud'"
    )
    if row is not None and row["laatst_op"]:
        previous = datetime.fromisoformat(str(row["laatst_op"]))
        if current - previous < timedelta(days=7):
            return False
    database.execute("PRAGMA optimize")
    database.execute("VACUUM")
    database.execute(
        """
        INSERT INTO onderhoud_status(sleutel,laatst_op,resultaat)
        VALUES ('sqlite_weekonderhoud',?,'VACUUM en PRAGMA optimize geslaagd')
        ON CONFLICT(sleutel) DO UPDATE SET laatst_op=excluded.laatst_op,
                                           resultaat=excluded.resultaat
        """,
        (current.isoformat(),),
    )
    return True


def run_maintenance(database: Database, settings: Settings) -> None:
    rotate_logs(database, settings)
    storage = storage_guard(database, settings)
    weekly_database_maintenance(database)
    automatic = database.fetch_one(
        "SELECT ingeschakeld FROM functie WHERE sleutel='automatische_backups'"
    )
    previous = database.fetch_one(
        "SELECT laatst_op FROM onderhoud_status WHERE sleutel='automatische_backup'"
    )
    now = datetime.now(UTC)
    last_backup = (
        datetime.fromisoformat(str(previous["laatst_op"]))
        if previous is not None and previous["laatst_op"]
        else None
    )
    if (
        automatic is not None
        and int(automatic["ingeschakeld"])
        and (last_backup is None or now - last_backup >= timedelta(days=1))
    ):
        create_backup(database, settings)
        enforce_retention(database)
        database.execute(
            """
            INSERT INTO onderhoud_status(sleutel,laatst_op,resultaat)
            VALUES ('automatische_backup',?,'back-up en retentie geslaagd')
            ON CONFLICT(sleutel) DO UPDATE SET laatst_op=excluded.laatst_op,
                                               resultaat=excluded.resultaat
            """,
            (now.isoformat(),),
        )
    database.execute(
        """
        INSERT INTO onderhoud_status(sleutel,laatst_op,resultaat)
        VALUES ('algemeen',?,?)
        ON CONFLICT(sleutel) DO UPDATE SET laatst_op=excluded.laatst_op,
                                           resultaat=excluded.resultaat
        """,
        (datetime.now(UTC).isoformat(), str(storage["status"])),
    )


async def maintenance_loop(database: Database, settings: Settings) -> None:
    while True:
        await asyncio.to_thread(run_maintenance, database, settings)
        await asyncio.sleep(3600)
