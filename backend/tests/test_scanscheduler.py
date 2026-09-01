from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import settings
from app.db import Database
from app.media.beslissen import STANDAARD
from app.scanner import ScanError, ScanSummary
from app.scanscheduler import LibraryScanScheduler, next_scan_at


def _database(tmp_path: Path) -> Database:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    configured = replace(
        settings,
        pipeline_root=tmp_path,
        workspace_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
    )
    database = Database(configured)
    database.initialize()
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES ('Test',?)", (json.dumps(STANDAARD),)
    )
    for index in range(2):
        library_path = tmp_path / f"library-{index}"
        library_path.mkdir()
        database.execute_insert(
            """
            INSERT INTO library(
                naam,pad,profiel_id,scan_interval,scan_tijd,verwacht_device,
                bron_type,bron_uri
            ) VALUES (?,?,?,86400,'03:00',?,'lokaal',?)
            """,
            (
                f"Library {index}",
                str(library_path),
                profile_id,
                library_path.stat().st_dev,
                str(library_path),
            ),
        )
    return database


def test_dagelijkse_scantijd_volgt_zomer_en_wintertijd() -> None:
    summer = next_scan_at(
        datetime(2026, 3, 28, 4, tzinfo=UTC), 86400, "03:00", "Europe/Amsterdam"
    )
    winter = next_scan_at(
        datetime(2026, 10, 24, 4, tzinfo=UTC), 86400, "03:00", "Europe/Amsterdam"
    )

    assert summer == datetime(2026, 3, 29, 1, tzinfo=UTC)
    assert winter == datetime(2026, 10, 25, 2, tzinfo=UTC)


def test_gemist_moment_wordt_niet_ingehaald() -> None:
    scheduled = next_scan_at(
        datetime(2026, 8, 31, 3, tzinfo=UTC), 86400, "03:00", "Europe/Amsterdam"
    )

    assert scheduled == datetime(2026, 9, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_libraries_worden_zonder_overlap_na_elkaar_gescand(tmp_path: Path) -> None:
    database = _database(tmp_path)
    active = 0
    maximum = 0
    order: list[int] = []

    def scan(_database: Database, library_id: int) -> ScanSummary:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        order.append(library_id)
        # De scheduler draait sync scans in een thread; een korte echte
        # wachttijd maakt overlap aantoonbaar zonder de productiescanner.
        time.sleep(0.02)
        active -= 1
        return ScanSummary(library_id=library_id, dry_run=False)

    scheduler = LibraryScanScheduler(database, scan)
    due = datetime(2026, 8, 31, 1, tzinfo=UTC)
    database.execute(
        "UPDATE library SET volgende_scan=?", ((due.replace(microsecond=0)).isoformat(),)
    )

    completed = await scheduler.tick(due)

    assert completed == [1, 2]
    assert order == [1, 2]
    assert maximum == 1


@pytest.mark.asyncio
async def test_scanfout_wacht_tot_volgende_schema(tmp_path: Path) -> None:
    database = _database(tmp_path)

    def fail(_database: Database, _library_id: int) -> ScanSummary:
        raise ScanError("SMB-mount ontbreekt")

    scheduler = LibraryScanScheduler(database, fail)
    due = datetime(2026, 8, 31, 1, tzinfo=UTC)
    database.execute("UPDATE library SET actief=0 WHERE id=2")
    database.execute("UPDATE library SET volgende_scan=? WHERE id=1", (due.isoformat(),))

    assert await scheduler.tick(due) == []
    row = database.fetch_one(
        "SELECT laatste_scan_status,laatste_scan_fout,volgende_scan FROM library WHERE id=1"
    )
    assert row is not None
    assert row["laatste_scan_status"] == "mislukt"
    assert row["laatste_scan_fout"] == "SMB-mount ontbreekt"
    assert datetime.fromisoformat(str(row["volgende_scan"])) > due


@pytest.mark.asyncio
async def test_twee_gelijktijdige_ticks_claimen_geen_tweede_scan(tmp_path: Path) -> None:
    database = _database(tmp_path)
    calls = 0

    def scan(_database: Database, library_id: int) -> ScanSummary:
        nonlocal calls
        calls += 1
        return ScanSummary(library_id=library_id, dry_run=False)

    scheduler = LibraryScanScheduler(database, scan)
    database.execute("UPDATE library SET actief=0 WHERE id=2")
    due = datetime(2026, 8, 31, 1, tzinfo=UTC)
    database.execute("UPDATE library SET volgende_scan=? WHERE id=1", (due.isoformat(),))

    await asyncio.gather(scheduler.tick(due), scheduler.tick(due))

    assert calls == 1


@pytest.mark.asyncio
async def test_schedulerloop_blijft_actief_na_tijdelijke_tickfout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduler = LibraryScanScheduler(_database(tmp_path))
    calls = 0

    async def flaky_tick(_now: datetime | None = None) -> list[int]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("tijdelijke SQLite-lock")
        scheduler.stopping = True
        return []

    monkeypatch.setattr(scheduler, "tick", flaky_tick)

    await asyncio.wait_for(scheduler.run_forever(interval=0.001), timeout=1)

    assert calls == 2
