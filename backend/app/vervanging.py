"""Crashvaste vervanging op het bibliotheekvolume, na verificatie."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.bestandsmetadata import FileMetadata, MetadataError, apply_metadata
from app.config import Settings
from app.db import Database
from app.media.probe import probe


class ReplacementError(RuntimeError):
    """De bron is veilig behouden omdat de vervangtransactie niet kon voltooien."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _set_status(database: Database, transaction_id: int, status: str, note: str = "") -> None:
    database.execute(
        "UPDATE vervang_transactie SET status=?,gewijzigd_op=?,toelichting=? WHERE id=?",
        (status, datetime.now(UTC).isoformat(), note or None, transaction_id),
    )


def atomic_replace(
    database: Database,
    job_id: int,
    source: Path,
    verified_result: Path,
    metadata: FileMetadata,
    *,
    after_step: Callable[[str], None] | None = None,
) -> None:
    source_stat = source.stat()
    temporary = source.with_name(f".{source.name}.av1-pipeline-{job_id}.deel")
    result_hash = file_sha256(verified_result)
    now = datetime.now(UTC).isoformat()
    transaction_id = database.execute_insert(
        """
        INSERT INTO vervang_transactie(
            job_id,status,bron_pad,tijdelijk_pad,bron_device,bron_inode,
            bron_grootte,bron_mtime,resultaat_sha256,gemaakt_op,gewijzigd_op
        ) VALUES (?, 'gestart', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            str(source),
            str(temporary),
            source_stat.st_dev,
            source_stat.st_ino,
            source_stat.st_size,
            source_stat.st_mtime,
            result_hash,
            now,
            now,
        ),
    )
    if after_step is not None:
        after_step("gestart")
    try:
        with verified_result.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        _fsync_directory(source.parent)
        _set_status(database, transaction_id, "resultaat_gekopieerd")
        if after_step is not None:
            after_step("resultaat_gekopieerd")

        current = source.stat()
        if (
            current.st_dev != source_stat.st_dev
            or current.st_ino != source_stat.st_ino
            or current.st_size != source_stat.st_size
            or current.st_mtime != source_stat.st_mtime
        ):
            raise ReplacementError("bron is tijdens de job gewijzigd")
        apply_metadata(temporary, metadata)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        _fsync_directory(source.parent)
        _set_status(database, transaction_id, "metadata_hersteld")
        if after_step is not None:
            after_step("metadata_hersteld")

        os.replace(temporary, source)
        _fsync_directory(source.parent)
        _set_status(database, transaction_id, "vervangen")
        if after_step is not None:
            after_step("vervangen")
    except Exception:
        # Alleen het verborgen resultaat wordt bij een fout opgeruimd. De bron
        # is vóór os.replace onaangeraakt; na os.replace staat het volledige,
        # geverifieerde resultaat al atomisch op het bronpad.
        if temporary.exists() and source.exists():
            temporary.unlink()
            _fsync_directory(source.parent)
        raise


def _source_matches(row: Any, source: Path) -> bool:
    if not source.exists():
        return False
    current = source.stat()
    return (
        current.st_dev == int(row["bron_device"])
        and current.st_ino == int(row["bron_inode"])
        and current.st_size == int(row["bron_grootte"])
        and current.st_mtime == float(row["bron_mtime"])
    )


def _finish_recovered_job(database: Database, job_id: int, source: Path) -> None:
    row = database.fetch_one(
        "SELECT j.bron_grootte,j.pogingen,f.id AS bestand_id,f.library_id FROM job j "
        "JOIN bestand f ON f.id=j.bestand_id WHERE j.id=?",
        (job_id,),
    )
    completed_at = datetime.now(UTC).isoformat()
    source_stat = source.stat()
    database.execute(
        """
        UPDATE job SET status='klaar',resultaat_pad=?,resultaat_grootte=?,percent=100,
                       klaar_op=?,proces_pid=NULL,cache_reservering=0 WHERE id=?
        """,
        (str(source), source_stat.st_size, completed_at, job_id),
    )
    result_probe = probe(str(source))
    if row is not None and result_probe is not None:
        database.execute(
            """
            UPDATE bestand
            SET grootte=?,mtime=?,nlink=?,device=?,inode=?,probe=?,gescand_op=?,
                aanwezig=1,stabiel_vanaf=?,scan_status='gereed'
            WHERE id=?
            """,
            (
                source_stat.st_size,
                source_stat.st_mtime,
                source_stat.st_nlink,
                source_stat.st_dev,
                source_stat.st_ino,
                json.dumps(result_probe, ensure_ascii=False),
                completed_at,
                completed_at,
                int(row["bestand_id"]),
            ),
        )
    if row is not None:
        database.execute(
            """
            UPDATE job_poging SET status='klaar',klaar_op=?
            WHERE job_id=? AND poging=?
            """,
            (completed_at, job_id, int(row["pogingen"])),
        )
    saving = database.fetch_one("SELECT id FROM besparing WHERE job_id=?", (job_id,))
    if row is not None and saving is None and row["bron_grootte"] is not None:
        database.execute(
            "INSERT INTO besparing(job_id,library_id,bytes_voor,bytes_na,dag) "
            "VALUES (?,?,?,?,date('now'))",
            (job_id, int(row["library_id"]), int(row["bron_grootte"]), source_stat.st_size),
        )


def recover_replacements(
    database: Database, settings: Settings | None = None
) -> list[dict[str, str]]:
    """Herstel ondubbelzinnige transacties en meld ieder conflict zonder te gissen."""

    outcomes: list[dict[str, str]] = []
    rows = database.fetch_all(
        """
        SELECT v.* FROM vervang_transactie v JOIN job j ON j.id=v.job_id
        WHERE v.status NOT IN ('hersteld','opgeruimd')
          AND (v.status!='vervangen' OR j.status!='klaar')
        ORDER BY v.id
        """
    )
    for row in rows:
        source = Path(str(row["bron_pad"]))
        temporary = Path(str(row["tijdelijk_pad"])) if row["tijdelijk_pad"] else None
        expected_hash = str(row["resultaat_sha256"] or "")
        transaction_id = int(row["id"])
        if source.exists() and expected_hash and file_sha256(source) == expected_hash:
            _set_status(database, transaction_id, "vervangen", "naamwissel was al voltooid")
            _finish_recovered_job(database, int(row["job_id"]), source)
            if settings is not None:
                shutil.rmtree(settings.cache_root / str(row["job_id"]), ignore_errors=True)
            outcomes.append({"pad": str(source), "status": "vervangen"})
            continue
        source_unchanged = _source_matches(row, source)
        if (
            source_unchanged
            and temporary is not None
            and temporary.is_file()
            and row["status"] in {"resultaat_gekopieerd", "metadata_hersteld"}
            and file_sha256(temporary) == expected_hash
        ):
            job = database.fetch_one(
                "SELECT bron_bestandsmetadata FROM job WHERE id=?", (int(row["job_id"]),)
            )
            if job is not None and job["bron_bestandsmetadata"]:
                try:
                    metadata = FileMetadata.from_json(str(job["bron_bestandsmetadata"]))
                    apply_metadata(temporary, metadata)
                    with temporary.open("rb") as handle:
                        os.fsync(handle.fileno())
                    _fsync_directory(source.parent)
                    os.replace(temporary, source)
                    _fsync_directory(source.parent)
                except (MetadataError, OSError, ValueError) as exc:
                    _set_status(database, transaction_id, "conflict", str(exc))
                    outcomes.append({"pad": str(source), "status": "conflict"})
                    continue
                _set_status(
                    database,
                    transaction_id,
                    "vervangen",
                    "geverifieerd tijdelijk resultaat na herstart afgemaakt",
                )
                _finish_recovered_job(database, int(row["job_id"]), source)
                if settings is not None:
                    shutil.rmtree(settings.cache_root / str(row["job_id"]), ignore_errors=True)
                outcomes.append({"pad": str(source), "status": "vervangen"})
                continue
        if source_unchanged and (temporary is None or not temporary.exists()):
            _set_status(database, transaction_id, "hersteld", "bron stond intact op zijn pad")
            database.execute(
                """
                UPDATE job SET status='wacht',worker_id=NULL,proces_pid=NULL,
                               cache_reservering=0,percent=0,
                               fout='vervanging na herstart hervatbaar'
                WHERE id=?
                """,
                (int(row["job_id"]),),
            )
            if settings is not None:
                shutil.rmtree(settings.cache_root / str(row["job_id"]), ignore_errors=True)
            outcomes.append({"pad": str(source), "status": "hersteld"})
            continue
        if (
            source_unchanged
            and temporary is not None
            and temporary.exists()
            and row["status"] == "gestart"
        ):
            # Status 'gestart' bewijst niet dat de kopie volledig is; alleen het
            # tijdelijke bestand mag daarom worden verwijderd.
            temporary.unlink()
            _fsync_directory(source.parent)
            _set_status(database, transaction_id, "hersteld", "onbewezen deelkopie verwijderd")
            database.execute(
                "UPDATE job SET status='wacht',proces_pid=NULL,cache_reservering=0 WHERE id=?",
                (int(row["job_id"]),),
            )
            outcomes.append({"pad": str(source), "status": "hersteld"})
            continue
        _set_status(database, transaction_id, "conflict", "toestand niet ondubbelzinnig")
        outcomes.append({"pad": str(source), "status": "conflict"})
    return outcomes
