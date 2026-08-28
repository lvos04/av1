"""Twee strikt gescheiden asyncio-banen met atomische SQLite-claims."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Any

import psutil

from app.config import Settings
from app.db import Database
from app.uitvoerder import run_job

Lane = str
MachineGuard = Callable[[Lane], tuple[bool, str | None]]
StorageValidator = Callable[[], None]
LOGGER = logging.getLogger(__name__)


def _temperature() -> float | None:
    temperatures = psutil.sensors_temperatures()
    values = [entry.current for entries in temperatures.values() for entry in entries]
    return max(values) if values else None


def default_machine_guard(database: Database, lane: Lane) -> tuple[bool, str | None]:
    if lane != "cpu":
        return True, None
    setting = database.fetch_one(
        "SELECT cpu_max_percent,cpu_max_temperatuur FROM instelling WHERE id=1"
    )
    if setting is None:
        return False, "algemene instellingen ontbreken"
    cpu = psutil.cpu_percent(interval=None)
    if cpu >= int(setting["cpu_max_percent"]):
        return False, f"CPU-gebruik {cpu:.0f}% boven limiet"
    temperature = _temperature()
    if temperature is not None and temperature >= int(setting["cpu_max_temperatuur"]):
        return False, f"CPU-temperatuur {temperature:.0f} °C boven limiet"
    return True, None


def _in_silence_window(start: str | None, end: str | None, now: datetime) -> bool:
    if not start or not end:
        return False
    start_time = time.fromisoformat(start)
    end_time = time.fromisoformat(end)
    current = now.timetz().replace(tzinfo=None)
    if start_time <= end_time:
        return start_time <= current < end_time
    return current >= start_time or current < end_time


def recover_orphaned_jobs(database: Database, settings: Settings) -> int:
    """Zet alleen aantoonbaar procesloze jobs terug; half output is nooit resultaat."""

    recovered = 0
    rows = database.fetch_all(
        "SELECT id,proces_pid FROM job WHERE status IN ('bezig','verifieren')"
    )
    for row in rows:
        pid = int(row["proces_pid"] or 0)
        alive = False
        if pid > 0 and psutil.pid_exists(pid):
            try:
                command = " ".join(psutil.Process(pid).cmdline())
                alive = "ffmpeg" in command or "mkvmerge" in command or "dovi_tool" in command
            except (psutil.Error, OSError):
                alive = False
        if alive:
            continue
        job_id = int(row["id"])
        cache_dir = settings.cache_root / str(job_id)
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir)
        database.execute(
            """
            UPDATE job SET status='wacht',worker_id=NULL,proces_pid=NULL,
                           cache_reservering=0,percent=0,fout='hersteld na backendherstart'
            WHERE id=?
            """,
            (job_id,),
        )
        recovered += 1
    return recovered


class Planner:
    """Plan maximaal het ingestelde aantal jobs zonder CPU/GPU te vermengen."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        storage_validator: StorageValidator | None = None,
        machine_guard: MachineGuard | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.storage_validator = storage_validator or settings.validate_storage
        self.machine_guard = machine_guard or (
            lambda lane: default_machine_guard(self.database, lane)
        )
        self.tasks: dict[int, asyncio.Task[bool]] = {}
        self.stopping = False

    def _lane_config(self, lane: Lane) -> Any:
        return self.database.fetch_one("SELECT * FROM worker_config WHERE baan=?", (lane,))

    def _lane_available(self, lane: Lane) -> tuple[bool, str | None]:
        config = self._lane_config(lane)
        if config is None:
            return False, "workerconfiguratie ontbreekt"
        if int(config["gepauzeerd"]):
            return False, str(config["pauze_reden"] or "handmatig gepauzeerd")
        if _in_silence_window(config["stilte_start"], config["stilte_einde"], datetime.now()):
            return False, "stiltevenster actief"
        return self.machine_guard(lane)

    def claim_next(self, lane: Lane, worker_id: str) -> tuple[int | None, str | None]:
        allowed, reason = self._lane_available(lane)
        if not allowed:
            return None, reason
        try:
            self.storage_validator()
        except Exception as exc:
            return None, f"opslagcontrole: {exc}"
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = int(
                connection.execute(
                    "SELECT COUNT(*) FROM job WHERE status IN ('bezig','verifieren')"
                ).fetchone()[0]
            )
            if active >= self.settings.max_active_jobs:
                connection.rollback()
                return None, "maximum actieve jobs bereikt"
            row = connection.execute(
                """
                SELECT j.id,f.grootte,f.library_id,l.canary_status
                FROM job j JOIN bestand f ON f.id=j.bestand_id
                JOIN library l ON l.id=f.library_id
                WHERE j.status='wacht' AND j.baan=?
                ORDER BY j.prioriteit DESC,j.id ASC LIMIT 1
                """,
                (lane,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None, None
            canary = connection.execute(
                "SELECT ingeschakeld,configuratie FROM functie WHERE sleutel='canary_limiet'"
            ).fetchone()
            if (
                canary is not None
                and int(canary["ingeschakeld"])
                and row["canary_status"] == "actief"
            ):
                configuration = json.loads(str(canary["configuratie"]))
                max_files = int(configuration.get("max_bestanden", 50))
                max_bytes = int(configuration.get("max_gb", 100)) * 1024**3
                max_today = int(configuration.get("max_jobs_per_dag", 10))
                completed = connection.execute(
                    """
                    SELECT COUNT(*) AS aantal,COALESCE(SUM(j.bron_grootte),0) AS bytes
                    FROM job j JOIN bestand f ON f.id=j.bestand_id
                    WHERE f.library_id=? AND j.status='klaar'
                    """,
                    (int(row["library_id"]),),
                ).fetchone()
                today = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM job j JOIN bestand f ON f.id=j.bestand_id
                        WHERE f.library_id=? AND date(j.gestart_op)=date('now')
                        """,
                        (int(row["library_id"]),),
                    ).fetchone()[0]
                )
                if (
                    int(completed["aantal"]) >= max_files
                    or int(completed["bytes"]) >= max_bytes
                    or today >= max_today
                ):
                    connection.execute(
                        "UPDATE job SET fout='wacht op expliciete vrijgave van "
                        "canary-batch' WHERE id=?",
                        (int(row["id"]),),
                    )
                    connection.commit()
                    return None, "canary-limiet bereikt"
            reserve = int(int(row["grootte"]) * self.settings.cache_reserve_factor)
            reserved = int(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(cache_reservering),0) FROM job
                    WHERE status IN ('bezig','verifieren')
                    """
                ).fetchone()[0]
            )
            setting = connection.execute(
                "SELECT cache_pauze_gb FROM instelling WHERE id=1"
            ).fetchone()
            pause_bytes = int(setting[0]) * 1024**3 if setting is not None else 10 * 1024**3
            free = shutil.disk_usage(self.settings.cache_root).free
            if free - reserved - reserve < pause_bytes:
                connection.execute(
                    "UPDATE job SET fout=? WHERE id=?",
                    ("wacht op voldoende gereserveerde cacheruimte", int(row["id"])),
                )
                connection.commit()
                return None, "onvoldoende vrije, niet-gereserveerde cacheruimte"
            cursor = connection.execute(
                """
                UPDATE job SET status='bezig',worker_id=?,gestart_op=?,pogingen=pogingen+1,
                               cache_reservering=?,fout=NULL
                WHERE id=? AND status='wacht'
                """,
                (
                    worker_id,
                    datetime.now(UTC).isoformat(),
                    reserve,
                    int(row["id"]),
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None, "job werd al door een andere planner geclaimd"
            connection.commit()
            return int(row["id"]), None

    async def _worker(self, job_id: int, worker_id: str) -> bool:
        return await run_job(
            self.database,
            self.settings,
            job_id,
            worker_id=worker_id,
            storage_validator=self.storage_validator,
            already_claimed=True,
        )

    def _remove_done(self) -> None:
        for job_id, task in list(self.tasks.items()):
            if task.done():
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    LOGGER.exception("Worker voor job %s stopte onverwacht", job_id)
                del self.tasks[job_id]

    async def tick(self) -> None:
        self._remove_done()
        active_by_lane: dict[str, int] = {"cpu": 0, "gpu": 0}
        if self.tasks:
            placeholders = ",".join("?" for _ in self.tasks)
            rows = self.database.fetch_all(
                f"""
                SELECT baan,COUNT(*) AS aantal FROM job
                WHERE id IN ({placeholders}) GROUP BY baan
                """,
                tuple(self.tasks),
            )
            for row in rows:
                active_by_lane[str(row["baan"])] = int(row["aantal"])
        for lane in ("cpu", "gpu"):
            config = self._lane_config(lane)
            desired = int(config["aantal"]) if config is not None else 1
            for slot in range(active_by_lane[lane], desired):
                worker_id = f"{lane}-{slot + 1}"
                job_id, _reason = self.claim_next(lane, worker_id)
                if job_id is None:
                    break
                self.tasks[job_id] = asyncio.create_task(self._worker(job_id, worker_id))

    async def run_forever(self, interval: float = 0.5) -> None:
        recover_orphaned_jobs(self.database, self.settings)
        while not self.stopping:
            await self.tick()
            await asyncio.sleep(interval)
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def stop(self) -> None:
        self.stopping = True
