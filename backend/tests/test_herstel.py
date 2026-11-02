from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from app.bestandsmetadata import capture_metadata
from app.config import Settings, settings
from app.db import Database
from app.vervanging import atomic_replace, file_sha256, recover_replacements


class PowerLoss(BaseException):
    """Bootst een proces- of stroomuitval na een persistente protocolstap na."""


def _case(
    tmp_path: Path, *, source_name: str = "film.mkv"
) -> tuple[Database, Settings, Path, Path, int, str, str]:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    source = library / source_name
    source.write_bytes(b"originele-media" * 100)
    source.chmod(0o640)
    os.setxattr(source, "user.av1-hersteltest", b"behouden")
    subprocess.run(["setfacl", "-m", "u:daemon:r--", str(source)], check=True)
    result_dir = roots["cache"] / "1"
    result_dir.mkdir()
    result = result_dir / "film.mkv"
    result.write_bytes(b"geverifieerde-av1-media" * 80)
    old_hash = file_sha256(source)
    new_hash = file_sha256(result)
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
    stat = source.stat()
    file_id = database.execute_insert(
        """
        INSERT INTO bestand(library_id,pad,grootte,mtime,categorie,gescand_op)
        VALUES (?,?,?,?, 'sdr',CURRENT_TIMESTAMP)
        """,
        (library_id, str(source), stat.st_size, stat.st_mtime),
    )
    decision_id = database.execute_insert(
        """
        INSERT INTO besluit(bestand_id,actie,baan,reden,payload,gemaakt_op)
        VALUES (?,'hercoderen','cpu','test','{}',CURRENT_TIMESTAMP)
        """,
        (file_id,),
    )
    job_id = database.execute_insert(
        """
        INSERT INTO job(
            besluit_id,bestand_id,baan,status,bron_grootte,bron_mtime,
            bron_bestandsmetadata,bron_device,bron_inode
        ) VALUES (?,?,'cpu','verifieren',?,?,?,?,?)
        """,
        (
            decision_id,
            file_id,
            stat.st_size,
            stat.st_mtime,
            capture_metadata(source).to_json(),
            stat.st_dev,
            stat.st_ino,
        ),
    )
    assert job_id == 1
    return database, configured, source, result, job_id, old_hash, new_hash


@pytest.mark.parametrize(
    ("crash_step", "expected_hash", "job_status"),
    [
        ("gestart", "oud", "wacht"),
        ("resultaat_gekopieerd", "nieuw", "klaar"),
        ("metadata_hersteld", "nieuw", "klaar"),
        ("vervangen", "nieuw", "klaar"),
    ],
)
def test_crash_na_iedere_vervangstap_is_ondubbelzinnig_herstelbaar(
    tmp_path: Path, crash_step: str, expected_hash: str, job_status: str
) -> None:
    database, configured, source, result, job_id, old_hash, new_hash = _case(tmp_path)
    metadata = capture_metadata(source)

    def crash(step: str) -> None:
        if step == crash_step:
            raise PowerLoss(step)

    with pytest.raises(PowerLoss):
        atomic_replace(database, job_id, source, result, metadata, after_step=crash)

    assert source.is_file()
    outcomes = recover_replacements(database, configured)
    assert outcomes and outcomes[0]["status"] in {"hersteld", "vervangen"}
    assert source.is_file()
    assert file_sha256(source) == (old_hash if expected_hash == "oud" else new_hash)
    row = database.fetch_one("SELECT status FROM job WHERE id=?", (job_id,))
    assert row is not None and row["status"] == job_status
    if expected_hash == "nieuw":
        assert source.stat().st_mode & 0o777 == 0o640
        assert os.getxattr(source, "user.av1-hersteltest") == b"behouden"
        acl = subprocess.run(
            ["getfacl", "--omit-header", str(source)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "user:daemon:r--" in acl


def test_gewijzigde_bron_wordt_conflict_en_nooit_een_automatische_gok(tmp_path: Path) -> None:
    database, configured, source, result, job_id, _old_hash, _new_hash = _case(tmp_path)

    def crash(step: str) -> None:
        if step == "resultaat_gekopieerd":
            raise PowerLoss(step)

    with pytest.raises(PowerLoss):
        atomic_replace(database, job_id, source, result, capture_metadata(source), after_step=crash)
    source.write_bytes(b"extern gewijzigd")
    changed_hash = file_sha256(source)

    assert recover_replacements(database, configured)[0]["status"] == "conflict"
    assert file_sha256(source) == changed_hash
    transaction = database.fetch_one("SELECT status,tijdelijk_pad FROM vervang_transactie")
    assert transaction is not None and transaction["status"] == "conflict"
    assert Path(str(transaction["tijdelijk_pad"])).is_file()


def test_herstel_laat_nieuwere_actieve_job_eigenaar_blijven(tmp_path: Path) -> None:
    database, configured, source, result, job_id, _old_hash, _new_hash = _case(tmp_path)

    def crash(step: str) -> None:
        if step == "gestart":
            raise PowerLoss(step)

    with pytest.raises(PowerLoss):
        atomic_replace(database, job_id, source, result, capture_metadata(source), after_step=crash)
    database.execute("UPDATE job SET status='fout' WHERE id=?", (job_id,))
    decision_id = database.execute_insert(
        """
        INSERT INTO besluit(bestand_id,actie,baan,reden,payload,gemaakt_op)
        SELECT bestand_id,'hercoderen','cpu','nieuwste besluit','{}',CURRENT_TIMESTAMP
        FROM job WHERE id=?
        """,
        (job_id,),
    )
    newer_job_id = database.execute_insert(
        """
        INSERT INTO job(besluit_id,bestand_id,baan,status)
        SELECT ?,bestand_id,'cpu','wacht' FROM job WHERE id=?
        """,
        (decision_id, job_id),
    )

    outcomes = recover_replacements(database, configured)

    assert outcomes[0]["status"] == "hersteld"
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (job_id,))[0] == (
        "overgeslagen"
    )
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (newer_job_id,))[0] == "wacht"


def test_vervanging_werkt_met_bronnaam_dicht_bij_name_max(tmp_path: Path) -> None:
    lange_naam = f"{'x' * 240}.mkv"
    database, _configured, source, result, job_id, _old_hash, new_hash = _case(
        tmp_path, source_name=lange_naam
    )

    atomic_replace(database, job_id, source, result, capture_metadata(source))

    assert source.name == lange_naam
    assert source.is_file()
    assert file_sha256(source) == new_hash
    transaction = database.fetch_one(
        "SELECT status,tijdelijk_pad FROM vervang_transactie WHERE job_id=?", (job_id,)
    )
    assert transaction is not None and transaction["status"] == "vervangen"
    assert len(Path(str(transaction["tijdelijk_pad"])).name.encode()) < 255
