"""Herstel uitsluitend aantoonbaar verweesde jobs en hun tijdelijke mappen."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import psutil

from app.config import Settings
from app.db import Database
from app.onderhoud import append_job_log

ACTIEVE_STATUSSEN = {"bezig", "verifieren"}
TIJDELIJKE_STATUSSEN = {
    "wacht",
    "bezig",
    "verifieren",
    "mislukt",
    "afgebroken",
    "overgeslagen",
}
MEDIA_PROCESSEN = {"ffmpeg", "mkvmerge", "dovi_tool"}


class QueueRecoveryError(RuntimeError):
    """Het herstel kan niet bewijzen dat tijdelijke data veilig verwijderbaar is."""


def media_process_alive(pid: int | None) -> bool:
    """Accepteer een PID alleen als die nog bij een bekende mediatool hoort."""

    if not pid or pid <= 0 or not psutil.pid_exists(pid):
        return False
    try:
        process = psutil.Process(pid)
        name = process.name()
        command = process.cmdline()
    except (psutil.Error, OSError):
        return False
    executable = Path(command[0]).name if command else name
    return name in MEDIA_PROCESSEN or executable in MEDIA_PROCESSEN


def _temporary_path(root: Path, job_id: int) -> Path:
    """Bouw alleen een numerieke kindmap onder de geconfigureerde tijdelijke root."""

    if job_id <= 0:
        raise QueueRecoveryError("Ongeldig jobnummer voor tijdelijk herstel")
    return root / str(job_id)


def _path_state(path: Path) -> tuple[bool, str | None]:
    if path.is_symlink():
        return True, f"Tijdelijk pad is onverwacht een symlink: {path}"
    try:
        if not path.exists():
            return False, None
        if not path.is_dir():
            return True, f"Tijdelijk pad is geen map: {path}"
    except OSError as exc:
        return True, f"Tijdelijk pad kan niet veilig worden gecontroleerd: {exc}"
    return True, None


def _remove_temporary_path(path: Path) -> bool:
    present, problem = _path_state(path)
    if problem is not None:
        raise QueueRecoveryError(problem)
    if not present:
        return False
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise QueueRecoveryError(
            f"Tijdelijke map kon niet worden verwijderd: {path}: {exc}"
        ) from exc
    return True


def remove_job_temporary_output(settings: Settings, job_id: int) -> list[str]:
    """Verwijder alleen cache/werk voor één exact bekend jobnummer."""

    removed: list[str] = []
    for root in (settings.cache_root, settings.work_root):
        path = _temporary_path(root, job_id)
        if _remove_temporary_path(path):
            removed.append(str(path))
    return removed


def remove_job_quality_output(settings: Settings, job_id: int) -> list[str]:
    """Verwijder alleen lokaal gemaakte vergelijkingsbeelden voor één job."""

    path = _temporary_path(settings.config_root / "kwaliteit", job_id)
    return [str(path)] if _remove_temporary_path(path) else []


def _known_job_ids(database: Database) -> set[int]:
    return {int(row["id"]) for row in database.fetch_all("SELECT id FROM job")}


def _unknown_temporary_paths(database: Database, settings: Settings) -> list[str]:
    """Meld onbekende inhoud, maar verwijder die nooit via algemeen herstel."""

    known = _known_job_ids(database)
    unknown: list[str] = []
    for root in (settings.cache_root, settings.work_root):
        try:
            children = list(root.iterdir())
        except OSError as exc:
            unknown.append(f"{root}: niet leesbaar: {exc}")
            continue
        for child in children:
            if not child.name.isdecimal() or int(child.name) not in known:
                unknown.append(str(child))
    return sorted(unknown)


def queue_recovery_report(
    database: Database,
    settings: Settings,
    active_task_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Geef een controleerbaar rapport zonder bronbestanden te openen."""

    task_ids = active_task_ids or set()
    memory = psutil.virtual_memory()
    active: list[int] = []
    orphaned: list[int] = []
    temporary: list[int] = []
    unsafe: list[str] = []
    rows = database.fetch_all(
        "SELECT id,status,proces_pid FROM job WHERE status IN "
        "('wacht','bezig','verifieren','mislukt','afgebroken','overgeslagen') ORDER BY id"
    )
    for row in rows:
        job_id = int(row["id"])
        status = str(row["status"])
        running = job_id in task_ids or media_process_alive(
            int(row["proces_pid"]) if row["proces_pid"] is not None else None
        )
        if running:
            active.append(job_id)
        elif status in ACTIEVE_STATUSSEN:
            orphaned.append(job_id)

        has_temporary = False
        for root in (settings.cache_root, settings.work_root):
            present, problem = _path_state(_temporary_path(root, job_id))
            has_temporary = has_temporary or present
            if problem is not None:
                unsafe.append(problem)
        if has_temporary and not running and status in TIJDELIJKE_STATUSSEN:
            temporary.append(job_id)

    limit_row = database.fetch_one(
        "SELECT max_gelijktijdige_encodes FROM instelling WHERE id=1"
    )
    total_limit = int(limit_row["max_gelijktijdige_encodes"]) if limit_row else 1
    unsafe_workers = [
        {
            "baan": str(row["baan"]),
            "aantal": int(row["aantal"]),
            "aanbevolen": total_limit,
        }
        for row in database.fetch_all(
            "SELECT baan,aantal FROM worker_config WHERE aantal>? ORDER BY baan",
            (total_limit,),
        )
    ]
    return {
        "actieve_jobs": active,
        "weesjobs": orphaned,
        "tijdelijke_jobs": sorted(set(temporary)),
        "onbekende_paden": _unknown_temporary_paths(database, settings),
        "onveilige_paden": sorted(set(unsafe)),
        "onveilige_workerinstellingen": unsafe_workers,
        "geheugen_totaal_bytes": int(memory.total),
        "geheugen_beschikbaar_bytes": int(memory.available),
        "kan_herstellen": not active
        and not unsafe
        and bool(orphaned or temporary or unsafe_workers),
        "uitleg": (
            "Alleen verweesde jobstatussen en tijdelijke cache-/werkmappen worden hersteld; "
            "bronbestanden, voltooide jobs en onbekende paden blijven onaangeraakt."
        ),
    }


def recover_queue(
    database: Database,
    settings: Settings,
    active_task_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Herstel de gerapporteerde veilige doelen; actieve jobs blokkeren alles."""

    report = queue_recovery_report(database, settings, active_task_ids)
    limit_row = database.fetch_one(
        "SELECT max_gelijktijdige_encodes FROM instelling WHERE id=1"
    )
    total_limit = int(limit_row["max_gelijktijdige_encodes"]) if limit_row else 1
    if report["actieve_jobs"]:
        raise QueueRecoveryError(
            "Wachtrijherstel is geweigerd omdat er nog een echte job actief is: "
            + ", ".join(f"#{job_id}" for job_id in report["actieve_jobs"])
        )
    if report["onveilige_paden"]:
        raise QueueRecoveryError(str(report["onveilige_paden"][0]))

    removed_paths = 0
    temporary_ids = {int(job_id) for job_id in report["tijdelijke_jobs"]}
    orphaned_ids = {int(job_id) for job_id in report["weesjobs"]}
    for job_id in sorted(temporary_ids):
        removed_paths += len(remove_job_temporary_output(settings, job_id))

    for job_id in sorted(orphaned_ids):
        database.execute(
            """
            UPDATE job SET status='wacht',worker_id=NULL,proces_pid=NULL,
                           cache_reservering=0,percent=0,afbreken_gevraagd=0,
                           fout='veilig hersteld via wachtrijherstel',foutsoort=NULL,
                           klaar_op=NULL
            WHERE id=? AND status IN ('bezig','verifieren')
            """,
            (job_id,),
        )
        append_job_log(
            database,
            job_id,
            "warning",
            "Verweesde job en uitsluitend tijdelijke output veilig hersteld via de UI.",
        )

    for job_id in sorted(temporary_ids - orphaned_ids):
        database.execute(
            """
            UPDATE job SET proces_pid=NULL,cache_reservering=0,worker_id=NULL
            WHERE id=? AND status IN ('wacht','mislukt','afgebroken','overgeslagen')
            """,
            (job_id,),
        )

    normalized_workers = [
        str(item["baan"]) for item in report["onveilige_workerinstellingen"]
    ]
    database.execute(
        "UPDATE worker_config SET aantal=? WHERE aantal>?", (total_limit, total_limit)
    )

    return {
        "herstelde_jobs": sorted(orphaned_ids),
        "opgeruimde_jobs": sorted(temporary_ids),
        "verwijderde_mappen": removed_paths,
        "herstelde_workerbanen": normalized_workers,
        "onbekende_paden_overgeslagen": report["onbekende_paden"],
        "uitleg": report["uitleg"],
    }


__all__ = [
    "QueueRecoveryError",
    "media_process_alive",
    "queue_recovery_report",
    "recover_queue",
    "remove_job_quality_output",
    "remove_job_temporary_output",
]
