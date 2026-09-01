"""Persistente scanschema's zonder root-cron of overlappende bibliotheekscans."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db import Database
from app.scanner import ScanError, ScanSummary, scan_library

LOGGER = logging.getLogger(__name__)
ScanFunction = Callable[[Database, int], ScanSummary]


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ScanError(f"Onbekende scantijdzone: {name}") from exc


def next_scan_at(
    now: datetime,
    interval: int,
    scan_time: str,
    timezone_name: str,
) -> datetime | None:
    """Geef uitsluitend een toekomstig moment; gemiste scans worden niet ingehaald."""

    if interval <= 0:
        return None
    zone = _timezone(timezone_name)
    local_now = now.astimezone(zone)
    hour, minute = (int(part) for part in scan_time.split(":"))
    target_time = time(hour=hour, minute=minute)
    if interval == 86400:
        candidate = datetime.combine(local_now.date(), target_time, zone)
        if candidate <= local_now:
            candidate += timedelta(days=1)
    elif interval == 604800:
        days_until_sunday = (6 - local_now.weekday()) % 7
        candidate = datetime.combine(
            local_now.date() + timedelta(days=days_until_sunday), target_time, zone
        )
        if candidate <= local_now:
            candidate += timedelta(days=7)
    else:
        candidate = local_now + timedelta(seconds=interval)
    return candidate.astimezone(UTC)


def record_scan_failure(database: Database, library_id: int, message: str) -> None:
    now = datetime.now(UTC).isoformat()
    changed = database.execute(
        """
        UPDATE scan_run
        SET status='mislukt',fout=?,klaar_op=?
        WHERE id=(
            SELECT id FROM scan_run
            WHERE library_id=? AND status='bezig'
            ORDER BY id DESC LIMIT 1
        )
        """,
        (message, now, library_id),
    )
    if changed == 0:
        database.execute_insert(
            """
            INSERT INTO scan_run(library_id,status,fout,gestart_op,klaar_op)
            VALUES (?,'mislukt',?,?,?)
            """,
            (library_id, message, now, now),
        )
    database.execute(
        """
        UPDATE library
        SET laatste_scan_status='mislukt',laatste_scan_fout=?,scan_cursor=NULL
        WHERE id=?
        """,
        (message, library_id),
    )


class LibraryScanScheduler:
    """Plan actieve libraries sequentieel op hun volgende lokale scanmoment."""

    def __init__(self, database: Database, scan_function: ScanFunction | None = None) -> None:
        self.database = database
        self.scan_function = scan_function or (
            lambda db, library_id: scan_library(db, library_id)
        )
        self.stopping = False
        self._lock = asyncio.Lock()
        self.current_library_id: int | None = None

    def _timezone_name(self) -> str:
        row = self.database.fetch_one("SELECT tijdzone FROM instelling WHERE id=1")
        return str(row["tijdzone"] if row is not None else "Europe/Amsterdam")

    def initialize_schedule(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        timezone_name = self._timezone_name()
        for row in self.database.fetch_all(
            "SELECT id,scan_interval,scan_tijd FROM library WHERE actief=1 ORDER BY id"
        ):
            next_run = next_scan_at(
                current,
                int(row["scan_interval"] or 0),
                str(row["scan_tijd"] or "03:00"),
                timezone_name,
            )
            self.database.execute(
                "UPDATE library SET volgende_scan=? WHERE id=?",
                (next_run.isoformat() if next_run is not None else None, int(row["id"])),
            )

    def schedule_library(self, library_id: int, now: datetime | None = None) -> None:
        row = self.database.fetch_one(
            "SELECT actief,scan_interval,scan_tijd FROM library WHERE id=?", (library_id,)
        )
        if row is None:
            return
        next_run = (
            next_scan_at(
                now or datetime.now(UTC),
                int(row["scan_interval"] or 0),
                str(row["scan_tijd"] or "03:00"),
                self._timezone_name(),
            )
            if bool(row["actief"])
            else None
        )
        self.database.execute(
            "UPDATE library SET volgende_scan=? WHERE id=?",
            (next_run.isoformat() if next_run is not None else None, library_id),
        )

    async def tick(self, now: datetime | None = None) -> list[int]:
        if self._lock.locked():
            return []
        async with self._lock:
            return await self._tick(now)

    async def _tick(self, now: datetime | None = None) -> list[int]:
        current = now or datetime.now(UTC)
        due = self.database.fetch_all(
            """
            SELECT id FROM library
            WHERE actief=1 AND scan_interval>0 AND volgende_scan IS NOT NULL
              AND volgende_scan<=?
            ORDER BY volgende_scan,id
            """,
            (current.isoformat(),),
        )
        completed: list[int] = []
        for row in due:
            library_id = int(row["id"])
            self.current_library_id = library_id
            try:
                self.database.execute(
                    """
                    UPDATE library
                    SET laatste_scan_status='bezig',laatste_scan_fout=NULL
                    WHERE id=?
                    """,
                    (library_id,),
                )
                try:
                    await asyncio.to_thread(self.scan_function, self.database, library_id)
                except (ScanError, OSError, ValueError) as exc:
                    message = str(exc)
                    record_scan_failure(self.database, library_id, message)
                    LOGGER.error(
                        "Automatische scan van library %s mislukt: %s", library_id, exc
                    )
                except Exception as exc:
                    message = f"onverwachte scanfout: {type(exc).__name__}"
                    record_scan_failure(self.database, library_id, message)
                    LOGGER.exception(
                        "Automatische scan van library %s stopte onverwacht", library_id
                    )
                else:
                    completed.append(library_id)
            finally:
                try:
                    self.schedule_library(library_id)
                finally:
                    self.current_library_id = None
        return completed

    async def run_forever(self, interval: float = 30.0) -> None:
        while not self.stopping:
            try:
                await self.tick()
            except Exception:
                # Ook fouten rond de scanclaim of het opnieuw plannen mogen de
                # dagelijkse scheduler niet permanent uitzetten.
                LOGGER.exception("Scanscheduler-tick stopte onverwacht; schema blijft actief")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wait_for_stop(), timeout=interval)

    async def _wait_for_stop(self) -> None:
        while not self.stopping:
            await asyncio.sleep(0.25)
