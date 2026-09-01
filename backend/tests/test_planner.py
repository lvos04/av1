from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from conftest import TESTDATA

from app.config import Settings, settings
from app.db import Database
from app.media.beslissen import STANDAARD
from app.planner import Planner, recover_orphaned_jobs
from app.scanner import scan_library
from app.uitvoerder import create_job_from_decision
from app.wachtrijherstel import QueueRecoveryError, queue_recovery_report, recover_queue


def _planner_case(tmp_path: Path) -> tuple[Database, Settings, list[int]]:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    shutil.copy2(TESTDATA / "hdr10.mkv", library / "hdr10.mkv")
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", library / "sdr-meertalig.mkv")
    configured = replace(
        settings,
        library_root=library,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
        max_active_jobs=2,
        storage_mode="aparte_schijf",
    )
    database = Database(configured)
    database.initialize()
    database.execute(
        "UPDATE instelling SET cache_pauze_gb=0,max_gelijktijdige_encodes=2 WHERE id=1"
    )
    profile = {
        **STANDAARD,
        "drempel_1080": 300,
        "preset_1080": 12,
        "crf_1080": 45,
        "gpu_quality": 35,
        "gpu_voor_sdr_1080": True,
    }
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES (?,?)", ("Test", json.dumps(profile))
    )
    database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id,verwacht_device) VALUES (?,?,?,?)",
        ("Test", str(library), profile_id, library.stat().st_dev),
    )
    scan_library(database, 1)
    decisions = database.fetch_all("SELECT id FROM besluit WHERE actie='hercoderen' ORDER BY id")
    job_ids: list[int] = []
    for decision in decisions:
        database.execute(
            "UPDATE besluit SET status='goedgekeurd' WHERE id=?", (int(decision["id"]),)
        )
        job_ids.append(create_job_from_decision(database, int(decision["id"])))
    assert len(job_ids) == 2
    return database, configured, job_ids


@pytest.mark.asyncio
async def test_cpu_en_gpu_baan_draaien_werkelijk_gelijktijdig(tmp_path: Path) -> None:
    database, configured, _job_ids = _planner_case(tmp_path)
    planner = Planner(
        database,
        configured,
        storage_validator=lambda: None,
        machine_guard=lambda _lane: (True, None),
    )

    await planner.tick()
    assert len(planner.tasks) == 2
    assert {row["baan"] for row in database.fetch_all("SELECT baan FROM job")} == {
        "cpu",
        "gpu",
    }
    await asyncio.gather(*planner.tasks.values())

    jobs = database.fetch_all("SELECT baan,status,gestart_op,klaar_op FROM job ORDER BY baan")
    assert {row["status"] for row in jobs} == {"klaar"}
    cpu = next(row for row in jobs if row["baan"] == "cpu")
    gpu = next(row for row in jobs if row["baan"] == "gpu")
    cpu_start = datetime.fromisoformat(str(cpu["gestart_op"]))
    cpu_end = datetime.fromisoformat(str(cpu["klaar_op"]))
    gpu_start = datetime.fromisoformat(str(gpu["gestart_op"]))
    gpu_end = datetime.fromisoformat(str(gpu["klaar_op"]))
    assert max(cpu_start, gpu_start) < min(cpu_end, gpu_end)


@pytest.mark.asyncio
async def test_pauze_van_cpu_laat_gpu_doordraaien(tmp_path: Path) -> None:
    database, configured, _job_ids = _planner_case(tmp_path)
    database.execute("UPDATE worker_config SET gepauzeerd=1,pauze_reden='test' WHERE baan='cpu'")
    planner = Planner(
        database,
        configured,
        storage_validator=lambda: None,
        machine_guard=lambda _lane: (True, None),
    )

    await planner.tick()
    assert len(planner.tasks) == 1
    active = database.fetch_one("SELECT baan FROM job WHERE status='bezig'")
    waiting = database.fetch_one("SELECT baan FROM job WHERE status='wacht'")
    assert active is not None and active["baan"] == "gpu"
    assert waiting is not None and waiting["baan"] == "cpu"
    await asyncio.gather(*planner.tasks.values())


@pytest.mark.asyncio
async def test_totale_grens_een_wisselt_eerlijk_tussen_cpu_en_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, configured, _job_ids = _planner_case(tmp_path)
    database.execute("UPDATE instelling SET max_gelijktijdige_encodes=1 WHERE id=1")
    planner = Planner(
        database,
        configured,
        storage_validator=lambda: None,
        machine_guard=lambda _lane: (True, None),
    )

    async def finish_immediately(job_id: int, _worker_id: str) -> bool:
        database.execute("UPDATE job SET status='klaar' WHERE id=?", (job_id,))
        return True

    monkeypatch.setattr(planner, "_worker", finish_immediately)

    await planner.tick()
    first = database.fetch_one("SELECT id,baan FROM job WHERE status='bezig'")
    assert first is not None and first["baan"] == "cpu"
    await asyncio.gather(*planner.tasks.values())

    # Simuleer een blijvende CPU-achterstand. Zonder roterende baanvolgorde
    # zou deze oudere CPU-job opnieuw winnen en kwam de GPU nooit aan bod.
    database.execute("UPDATE job SET status='wacht' WHERE id=?", (int(first["id"]),))
    await planner.tick()

    second = database.fetch_one("SELECT baan FROM job WHERE status='bezig'")
    assert second is not None and second["baan"] == "gpu"
    await asyncio.gather(*planner.tasks.values())


@pytest.mark.asyncio
async def test_twee_planners_claimen_dezelfde_job_nooit_dubbel(tmp_path: Path) -> None:
    database, configured, _job_ids = _planner_case(tmp_path)
    database.execute("DELETE FROM job WHERE baan='gpu'")
    first = Planner(
        database,
        configured,
        storage_validator=lambda: None,
        machine_guard=lambda _lane: (True, None),
    )
    second = Planner(
        database,
        configured,
        storage_validator=lambda: None,
        machine_guard=lambda _lane: (True, None),
    )

    claims = await asyncio.gather(
        asyncio.to_thread(first.claim_next, "cpu", "eerste"),
        asyncio.to_thread(second.claim_next, "cpu", "tweede"),
    )

    assert sum(job_id is not None for job_id, _reason in claims) == 1
    row = database.fetch_one("SELECT status,pogingen FROM job")
    assert row is not None and row["status"] == "bezig" and row["pogingen"] == 1


def test_backendherstart_herstelt_weesjob_en_deelbestand(tmp_path: Path) -> None:
    database, configured, job_ids = _planner_case(tmp_path)
    job_id = job_ids[0]
    database.execute(
        "UPDATE job SET status='bezig',proces_pid=999999,cache_reservering=123 WHERE id=?",
        (job_id,),
    )
    cache_dir = configured.cache_root / str(job_id)
    cache_dir.mkdir()
    (cache_dir / "film.mkv.deel").write_bytes(b"onvolledig")

    assert recover_orphaned_jobs(database, configured) == 1
    row = database.fetch_one(
        "SELECT status,proces_pid,cache_reservering FROM job WHERE id=?", (job_id,)
    )
    assert row is not None and row["status"] == "wacht"
    assert row["proces_pid"] is None and row["cache_reservering"] == 0
    assert not cache_dir.exists()


def test_backendherstart_ruimt_retrycache_van_wachtende_job_op(tmp_path: Path) -> None:
    database, configured, job_ids = _planner_case(tmp_path)
    job_id = job_ids[0]
    cache_dir = configured.cache_root / str(job_id)
    work_dir = configured.work_root / str(job_id)
    cache_dir.mkdir()
    work_dir.mkdir()
    (cache_dir / "film.mkv.deel").write_bytes(b"oude retrycache")

    assert recover_orphaned_jobs(database, configured) == 0

    assert not cache_dir.exists() and not work_dir.exists()
    row = database.fetch_one(
        "SELECT status,proces_pid,cache_reservering FROM job WHERE id=?", (job_id,)
    )
    assert row is not None and row["status"] == "wacht"
    assert row["proces_pid"] is None and row["cache_reservering"] == 0


def test_ui_wachtrijherstel_raakt_alleen_bekende_tijdelijke_jobmappen(
    tmp_path: Path,
) -> None:
    database, configured, job_ids = _planner_case(tmp_path)
    job_id = job_ids[0]
    source = configured.library_root / "hdr10.mkv"
    source_before = source.read_bytes()
    database.execute(
        "UPDATE job SET status='bezig',proces_pid=999999,cache_reservering=123 WHERE id=?",
        (job_id,),
    )
    cache_dir = configured.cache_root / str(job_id)
    work_dir = configured.work_root / str(job_id)
    cache_dir.mkdir()
    work_dir.mkdir()
    (cache_dir / "film.mkv.deel").write_bytes(b"onvolledig")
    unknown = configured.cache_root / "handmatig-bewaard"
    unknown.mkdir()
    (unknown / "notitie.txt").write_text("niet verwijderen", encoding="utf-8")

    report = queue_recovery_report(database, configured)
    assert report["weesjobs"] == [job_id]
    assert report["tijdelijke_jobs"] == [job_id]
    assert str(unknown) in report["onbekende_paden"]
    assert report["kan_herstellen"] is True

    result = recover_queue(database, configured)

    assert result["herstelde_jobs"] == [job_id]
    assert not cache_dir.exists() and not work_dir.exists()
    assert unknown.is_dir() and (unknown / "notitie.txt").is_file()
    assert source.read_bytes() == source_before
    row = database.fetch_one(
        "SELECT status,proces_pid,cache_reservering FROM job WHERE id=?", (job_id,)
    )
    assert row is not None and row["status"] == "wacht"
    assert row["proces_pid"] is None and row["cache_reservering"] == 0


def test_ui_wachtrijherstel_weigert_actieve_plannertaak(tmp_path: Path) -> None:
    database, configured, job_ids = _planner_case(tmp_path)
    job_id = job_ids[0]
    database.execute("UPDATE job SET status='bezig' WHERE id=?", (job_id,))
    cache_dir = configured.cache_root / str(job_id)
    cache_dir.mkdir()

    report = queue_recovery_report(database, configured, {job_id})
    assert report["actieve_jobs"] == [job_id]
    assert report["kan_herstellen"] is False
    with pytest.raises(QueueRecoveryError, match="echte job actief"):
        recover_queue(database, configured, {job_id})
    assert cache_dir.is_dir()


@pytest.mark.asyncio
async def test_onverwachte_startfout_blijft_niet_als_bezig_staan(tmp_path: Path) -> None:
    database, configured, job_ids = _planner_case(tmp_path)
    job_id = job_ids[0]
    database.execute(
        "UPDATE job SET status='bezig',cache_reservering=123 WHERE id=?", (job_id,)
    )
    (configured.cache_root / str(job_id)).mkdir()
    planner = Planner(
        database,
        configured,
        storage_validator=lambda: None,
        machine_guard=lambda _lane: (True, None),
    )

    assert not await planner._worker(job_id, "cpu-1")

    row = database.fetch_one(
        "SELECT status,foutsoort,cache_reservering FROM job WHERE id=?", (job_id,)
    )
    assert row is not None and row["status"] == "mislukt"
    assert row["foutsoort"] == "FileExistsError"
    assert row["cache_reservering"] == 0


def test_twee_claims_reserveren_samen_en_een_derde_wacht_eerlijk(tmp_path: Path) -> None:
    database, configured, _job_ids = _planner_case(tmp_path)
    first = database.fetch_one(
        "SELECT f.*,b.payload,b.reden,b.encoder FROM bestand f "
        "JOIN besluit b ON b.bestand_id=f.id ORDER BY f.id LIMIT 1"
    )
    assert first is not None
    file_id = database.execute_insert(
        """
        INSERT INTO bestand(library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op)
        VALUES (?,?,?,?,1,?,?,CURRENT_TIMESTAMP)
        """,
        (
            first["library_id"],
            str(configured.library_root / "derde.mkv"),
            first["grootte"],
            first["mtime"],
            first["categorie"],
            first["probe"],
        ),
    )
    decision_id = database.execute_insert(
        """
        INSERT INTO besluit(
            bestand_id,actie,baan,encoder,reden,payload,status,gemaakt_op
        ) VALUES (?,'hercoderen','cpu','libsvtav1',?,?,'ingepland',CURRENT_TIMESTAMP)
        """,
        (file_id, first["reden"], first["payload"]),
    )
    database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
        (decision_id, file_id),
    )
    planner = Planner(
        database,
        configured,
        storage_validator=lambda: None,
        machine_guard=lambda _lane: (True, None),
    )

    cpu_job, _ = planner.claim_next("cpu", "cpu-1")
    gpu_job, _ = planner.claim_next("gpu", "gpu-1")
    third, reason = planner.claim_next("cpu", "cpu-2")

    assert cpu_job is not None and gpu_job is not None
    assert third is None and reason is not None and "encodegrens van 2" in reason
    reservations = database.fetch_one(
        "SELECT SUM(cache_reservering) AS totaal FROM job WHERE status='bezig'"
    )
    assert reservations is not None
    expected = sum(
        int(row["grootte"] * configured.cache_reserve_factor)
        for row in database.fetch_all(
            "SELECT f.grootte FROM job j JOIN bestand f ON f.id=j.bestand_id WHERE j.status='bezig'"
        )
    )
    assert reservations["totaal"] == expected


def test_totale_encodegrens_is_direct_instelbaar_zonder_harde_ramgrens(tmp_path: Path) -> None:
    database, configured, _job_ids = _planner_case(tmp_path)
    database.execute(
        "UPDATE instelling SET max_gelijktijdige_encodes=1 WHERE id=1"
    )
    planner = Planner(
        database,
        configured,
        storage_validator=lambda: None,
        machine_guard=lambda _lane: (True, None),
    )

    first, _ = planner.claim_next("gpu", "gpu-1")
    second, reason = planner.claim_next("cpu", "cpu-1")

    assert first is not None
    assert second is None
    assert reason is not None and "encodegrens van 1" in reason

    database.execute(
        "UPDATE instelling SET max_gelijktijdige_encodes=2 WHERE id=1"
    )
    second, reason = planner.claim_next("cpu", "cpu-1")

    assert second is not None
    assert reason is None


@pytest.mark.asyncio
async def test_plannerloop_overleeft_tijdelijke_sqlite_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, configured, _job_ids = _planner_case(tmp_path)
    planner = Planner(
        database,
        configured,
        storage_validator=lambda: None,
        machine_guard=lambda _lane: (True, None),
    )
    calls = 0

    async def flaky_tick() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        planner.stopping = True

    monkeypatch.setattr(planner, "tick", flaky_tick)

    await asyncio.wait_for(planner.run_forever(interval=0.001), timeout=1)

    assert calls == 2
