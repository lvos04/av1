from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import TESTDATA

from app.config import Settings, settings
from app.db import Database
from app.media.beslissen import STANDAARD
from app.media.probe import probe
from app.scanner import scan_library
from app.uitvoerder import create_job_from_decision, run_job
from app.verificatie import VerificationResult
from app.vervanging import file_sha256


def _case(tmp_path: Path, source_name: str = "hdr10.mkv") -> tuple[Database, Settings, Path, int]:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    source = library / source_name
    shutil.copy2(TESTDATA / source_name, source)
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
    profile = {
        **STANDAARD,
        "drempel_1080": 500,
        "drempel_720": 100,
        "preset_1080": 12,
        "crf_1080": 45,
        "gpu_voor_sdr_1080": False,
    }
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES (?,?)", ("Test", json.dumps(profile))
    )
    database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id,verwacht_device) VALUES (?,?,?,?)",
        ("Test", str(library), profile_id, library.stat().st_dev),
    )
    scan_library(database, 1)
    decision = database.fetch_one(
        "SELECT id FROM besluit WHERE actie='hercoderen' ORDER BY id LIMIT 1"
    )
    assert decision is not None
    decision_id = int(decision["id"])
    database.execute("UPDATE besluit SET status='goedgekeurd' WHERE id=?", (decision_id,))
    return database, configured, source, create_job_from_decision(database, decision_id)


@pytest.mark.asyncio
async def test_hdr_wordt_pas_na_verificatie_atomisch_vervangen(tmp_path: Path) -> None:
    database, configured, source, job_id = _case(tmp_path)
    source.chmod(0o640)
    source_xattr = b"blijft-behouden"
    os.setxattr(source, "user.av1-test", source_xattr)
    subprocess.run(["setfacl", "-m", "u:daemon:r--", str(source)], check=True)
    before_hash = file_sha256(source)

    assert await run_job(database, configured, job_id, storage_validator=lambda: None)

    result_probe = probe(str(source))
    assert result_probe is not None and result_probe["is_hdr10"] is True
    assert result_probe["codec"] == "av1"
    assert file_sha256(source) != before_hash
    assert os.getxattr(source, "user.av1-test") == source_xattr
    assert source.stat().st_mode & 0o777 == 0o640
    acl = subprocess.run(
        ["getfacl", "--omit-header", str(source)], capture_output=True, text=True, check=True
    ).stdout
    assert "user:daemon:r--" in acl
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (job_id,))[0] == "klaar"
    assert (
        database.fetch_one("SELECT status FROM vervang_transactie WHERE job_id=?", (job_id,))[0]
        == "vervangen"
    )


@pytest.mark.asyncio
async def test_mislukte_verificatie_laat_bron_bytegelijk_staan(tmp_path: Path) -> None:
    database, configured, source, job_id = _case(tmp_path)
    before_hash = file_sha256(source)

    def reject(result: VerificationResult) -> VerificationResult:
        return replace(result, passed=False, reason="geforceerde SDR-regressie")

    assert not await run_job(
        database,
        configured,
        job_id,
        storage_validator=lambda: None,
        verification_hook=reject,
    )

    assert file_sha256(source) == before_hash
    job = database.fetch_one("SELECT status,fout FROM job WHERE id=?", (job_id,))
    assert job is not None and job["status"] == "mislukt"
    assert "verificatie" in str(job["fout"])
    assert any((configured.cache_root / str(job_id)).iterdir())


@pytest.mark.asyncio
async def test_afbreken_ruimt_cache_op_en_raakt_bron_niet(tmp_path: Path) -> None:
    database, configured, source, job_id = _case(tmp_path)
    before_hash = file_sha256(source)
    task = asyncio.create_task(
        run_job(
            database,
            configured,
            job_id,
            storage_validator=lambda: None,
            command_override=["/usr/bin/python3", "-c", "import time; time.sleep(10)"],
        )
    )
    for _ in range(50):
        row = database.fetch_one("SELECT proces_pid FROM job WHERE id=?", (job_id,))
        if row is not None and row[0]:
            break
        await asyncio.sleep(0.05)
    database.execute("UPDATE job SET afbreken_gevraagd=1 WHERE id=?", (job_id,))

    assert not await asyncio.wait_for(task, timeout=5)
    assert file_sha256(source) == before_hash
    assert not (configured.cache_root / str(job_id)).exists()
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (job_id,))[0] == "afgebroken"


@pytest.mark.asyncio
async def test_explicitiet_bevestigde_hardlink_wordt_alleen_op_bronpad_verbroken(
    tmp_path: Path,
) -> None:
    database, configured, source, job_id = _case(tmp_path, "sdr-meertalig.mkv")
    torrent = source.with_name("torrent-seed.mkv")
    torrent.hardlink_to(source)
    old_hash = file_sha256(source)
    database.execute(
        "UPDATE bestand SET nlink=2 WHERE id=(SELECT bestand_id FROM job WHERE id=?)",
        (job_id,),
    )
    database.execute(
        "UPDATE besluit SET hardlink_bevestigd=1 WHERE id=(SELECT besluit_id FROM job WHERE id=?)",
        (job_id,),
    )
    database.execute("UPDATE instelling SET hardlink_modus='verbreek_na_bevestiging' WHERE id=1")
    database.execute("UPDATE functie SET ingeschakeld=1 WHERE sleutel='hardlink_verbreken'")

    assert await run_job(database, configured, job_id, storage_validator=lambda: None)

    assert file_sha256(torrent) == old_hash
    assert file_sha256(source) != old_hash
    assert source.stat().st_ino != torrent.stat().st_ino
    assert source.stat().st_nlink == 1 and torrent.stat().st_nlink == 1
