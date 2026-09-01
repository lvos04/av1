"""Hervatbare, uitsluitend lezende bibliotheekscanner."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.beslisser import decide_catalog_item, store_decision
from app.bronnen import SourceError, validate_registered_source
from app.db import Database
from app.media.bestandsfilter import VIDEO_EXT, is_hoofdbestand
from app.media.probe import probe


class ScanError(RuntimeError):
    """Een library of profiel is ongeldig."""


SCAN_LOCK = threading.Lock()
FILE_STABILITY_SECONDS = 3600


def _decision_code_fingerprint() -> str:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in (
        Path(__file__),
        root / "beslisser.py",
        root / "media" / "beslissen.py",
        root / "media" / "bestandsfilter.py",
    ):
        digest.update(path.read_bytes())
    return digest.hexdigest()


DECISION_CODE_FINGERPRINT = _decision_code_fingerprint()


@dataclass(slots=True)
class ScanItem:
    pad: str
    categorie: str
    filter_reden: str
    actie: str
    besluit_reden: str
    nlink: int


@dataclass(slots=True)
class ScanSummary:
    library_id: int
    dry_run: bool
    gevonden: int = 0
    nieuw: int = 0
    gewijzigd: int = 0
    verwijderd: int = 0
    uitgesteld: int = 0
    hervat: bool = False
    items: list[ScanItem] = field(default_factory=list)


def _library(database: Database, library_id: int) -> tuple[Any, dict[str, Any]]:
    row = database.fetch_one(
        """
        SELECT l.*, p.inst AS profiel_inst
        FROM library l JOIN profiel p ON p.id=l.profiel_id
        WHERE l.id=? AND l.actief=1
        """,
        (library_id,),
    )
    if row is None:
        raise ScanError(f"Actieve library {library_id} bestaat niet")
    profile_value = json.loads(str(row["profiel_inst"]))
    if not isinstance(profile_value, dict):
        raise ScanError("Profielinstellingen moeten een JSON-object zijn")
    return row, profile_value


def _directories(root: Path) -> list[Path]:
    directories: list[Path] = []
    for current, names, _files in os.walk(root):
        names.sort()
        directories.append(Path(current))
    return sorted(directories, key=lambda path: str(path))


def scan_library(
    database: Database,
    library_id: int,
    *,
    dry_run: bool = False,
    stop_after_directories: int | None = None,
) -> ScanSummary:
    """Scan per map en commit onderweg; een test kan gecontroleerd onderbreken."""

    if not SCAN_LOCK.acquire(blocking=False):
        raise ScanError("Er draait al een bibliotheekscan")
    try:
        return _scan_library_locked(
            database,
            library_id,
            dry_run=dry_run,
            stop_after_directories=stop_after_directories,
        )
    finally:
        SCAN_LOCK.release()


def _scan_library_locked(
    database: Database,
    library_id: int,
    *,
    dry_run: bool,
    stop_after_directories: int | None,
) -> ScanSummary:
    """Voer één procesbrede scan uit; SQLite bewaakt daarnaast de libraryclaim."""

    library, profile = _library(database, library_id)
    profile_fingerprint = hashlib.sha256(
        json.dumps(
            {"profiel": profile, "besliscode": DECISION_CODE_FINGERPRINT},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    root = Path(str(library["pad"]))
    if not root.is_dir():
        raise ScanError(f"Librarypad bestaat niet of is geen map: {root}")
    try:
        current_device = validate_registered_source(
            root,
            source_type=str(library["bron_type"]),
            source_uri=str(library["bron_uri"]) if library["bron_uri"] else None,
            expected_device=(
                int(library["verwacht_device"]) if library["verwacht_device"] is not None else None
            ),
        )
    except SourceError as exc:
        raise ScanError(str(exc)) from exc
    if str(library["bron_type"]) == "smb" and current_device != library["verwacht_device"]:
        database.execute(
            "UPDATE library SET verwacht_device=? WHERE id=?", (current_device, library_id)
        )

    summary = ScanSummary(library_id=library_id, dry_run=dry_run)
    scan_id: int | None = None
    cursor_path = ""
    started_at = datetime.now(UTC).isoformat()
    if not dry_run:
        current = database.fetch_one(
            "SELECT * FROM scan_run WHERE library_id=? AND status='bezig' ORDER BY id DESC LIMIT 1",
            (library_id,),
        )
        if current is not None:
            scan_id = int(current["id"])
            cursor_path = str(current["cursor"] or "")
            started_at = str(current["gestart_op"])
            summary.hervat = True
        else:
            try:
                scan_id = database.execute_insert(
                    "INSERT INTO scan_run(library_id,status,gestart_op) VALUES (?, 'bezig', ?)",
                    (library_id, started_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ScanError("Voor deze library draait al een scan") from exc
        database.execute(
            "UPDATE library SET laatste_scan_status='bezig',laatste_scan_fout=NULL WHERE id=?",
            (library_id,),
        )

    processed_directories = 0
    for directory in _directories(root):
        directory_text = str(directory)
        if cursor_path and directory_text <= cursor_path:
            continue
        entries = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
        with database.connection() as connection:
            for path in entries:
                if not path.is_file() or path.suffix.lower() not in VIDEO_EXT:
                    continue
                stat = path.stat()
                _process_file(
                    connection,
                    library_id,
                    path,
                    stat,
                    profile,
                    profile_fingerprint,
                    started_at,
                    dry_run,
                    summary,
                )
            if not dry_run and scan_id is not None:
                connection.execute(
                    "UPDATE scan_run SET cursor=?, gevonden=? WHERE id=?",
                    (directory_text, summary.gevonden, scan_id),
                )
                connection.execute(
                    "UPDATE library SET scan_cursor=? WHERE id=?", (directory_text, library_id)
                )
                connection.commit()
        processed_directories += 1
        if stop_after_directories is not None and processed_directories >= stop_after_directories:
            return summary

    if not dry_run and scan_id is not None:
        with database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE bestand
                SET aanwezig=0,scan_status='verdwenen'
                WHERE library_id=? AND gescand_op < ? AND aanwezig=1
                """,
                (library_id, started_at),
            )
            summary.verwijderd = cursor.rowcount
            connection.execute(
                """
                UPDATE besluit
                SET status='afgewezen',reden=reden || '; bron niet meer aanwezig'
                WHERE status='nieuw' AND bestand_id IN (
                    SELECT id FROM bestand WHERE library_id=? AND aanwezig=0
                )
                """,
                (library_id,),
            )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                UPDATE scan_run
                SET status='klaar',klaar_op=?,gevonden=?,nieuw=?,gewijzigd=?,
                    verdwenen=?,uitgesteld=?
                WHERE id=?
                """,
                (
                    now,
                    summary.gevonden,
                    summary.nieuw,
                    summary.gewijzigd,
                    summary.verwijderd,
                    summary.uitgesteld,
                    scan_id,
                ),
            )
            connection.execute(
                """
                UPDATE library
                SET laatste_scan=?,scan_cursor=NULL,laatste_scan_status='klaar',
                    laatste_scan_fout=NULL
                WHERE id=?
                """,
                (now, library_id),
            )
            connection.commit()
    return summary


def _process_file(
    connection: Any,
    library_id: int,
    path: Path,
    stat: os.stat_result,
    profile: dict[str, Any],
    profile_fingerprint: str,
    scan_marker: str,
    dry_run: bool,
    summary: ScanSummary,
) -> None:
    existing = connection.execute(
        """
        SELECT id,grootte,mtime,nlink,device,inode,categorie,probe,aanwezig,
               profiel_fingerprint,scan_status
        FROM bestand WHERE pad=?
        """,
        (str(path),),
    ).fetchone()
    metadata_unchanged = bool(
        existing is not None
        and int(existing["grootte"]) == stat.st_size
        and float(existing["mtime"]) == stat.st_mtime
        and int(existing["nlink"]) == stat.st_nlink
        and (existing["device"] is None or int(existing["device"]) == stat.st_dev)
        and (existing["inode"] is None or int(existing["inode"]) == stat.st_ino)
        and int(existing["aanwezig"])
    )
    profile_unchanged = bool(
        existing is not None and existing["profiel_fingerprint"] == profile_fingerprint
    )

    if existing is not None:
        active = connection.execute(
            """
            SELECT 1 FROM job
            WHERE bestand_id=? AND status IN ('wacht','bezig','verifieren')
            LIMIT 1
            """,
            (int(existing["id"]),),
        ).fetchone()
        if active is not None:
            if not dry_run:
                connection.execute(
                    "UPDATE bestand SET gescand_op=?,aanwezig=1 WHERE id=?",
                    (scan_marker, int(existing["id"])),
                )
            summary.gevonden += 1
            summary.items.append(
                ScanItem(
                    pad=str(path),
                    categorie=str(existing["categorie"]),
                    filter_reden="actieve job",
                    actie="overslaan",
                    besluit_reden="actieve job blijft eigenaar van dit bronpad",
                    nlink=stat.st_nlink,
                )
            )
            return

    if (
        metadata_unchanged
        and profile_unchanged
        and existing["scan_status"] == "gereed"
        and not dry_run
    ):
        latest = connection.execute(
            "SELECT actie,reden FROM besluit WHERE bestand_id=? ORDER BY id DESC LIMIT 1",
            (int(existing["id"]),),
        ).fetchone()
        connection.execute(
            """
            UPDATE bestand
            SET gescand_op=?,aanwezig=1,scan_status='gereed',device=?,inode=?
            WHERE id=?
            """,
            (scan_marker, stat.st_dev, stat.st_ino, int(existing["id"])),
        )
        summary.gevonden += 1
        summary.items.append(
            ScanItem(
                pad=str(path),
                categorie=str(existing["categorie"]),
                filter_reden="ongewijzigd",
                actie=str(latest["actie"] if latest is not None else "overslaan"),
                besluit_reden=str(
                    latest["reden"] if latest is not None else "ongewijzigd catalogusbestand"
                ),
                nlink=stat.st_nlink,
            )
        )
        return

    if time.time() - stat.st_mtime < FILE_STABILITY_SECONDS:
        _store_deferred_file(
            connection,
            library_id,
            path,
            stat,
            existing,
            profile_fingerprint,
            scan_marker,
            dry_run,
        )
        summary.gevonden += 1
        summary.uitgesteld += 1
        if existing is None:
            summary.nieuw += 1
        elif not metadata_unchanged:
            summary.gewijzigd += 1
        summary.items.append(
            ScanItem(
                pad=str(path),
                categorie=str(existing["categorie"] if existing is not None else "wacht"),
                filter_reden="bestand is minder dan 60 minuten stabiel",
                actie="overslaan",
                besluit_reden="wacht op stabiliteitsvenster",
                nlink=stat.st_nlink,
            )
        )
        return

    # Een vorige bestandsobservatie kan deze maptransactie al schrijvend hebben
    # gemaakt. Geef die lock vrij voordat de duur- en mediaprobes starten: op
    # grote SMB-bestanden kunnen die minuten duren en de planner mag ondertussen
    # gewoon een reeds goedgekeurde job claimen.
    connection.commit()
    ok, filter_reason, category = is_hoofdbestand(
        str(path), float(profile.get("min_duur_sec", 0))
    )
    del ok
    if (
        metadata_unchanged
        and existing is not None
        and existing["scan_status"] == "gereed"
        and existing["probe"]
    ):
        parsed_probe = json.loads(str(existing["probe"]))
        probe_data = parsed_probe if isinstance(parsed_probe, dict) else None
    else:
        probe_data = probe(str(path)) if category in {"ok", "extra"} else None

    after = path.stat()
    if (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime,
        after.st_nlink,
    ) != (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime, stat.st_nlink):
        _store_deferred_file(
            connection,
            library_id,
            path,
            after,
            existing,
            profile_fingerprint,
            scan_marker,
            dry_run,
        )
        summary.gevonden += 1
        summary.uitgesteld += 1
        if existing is None:
            summary.nieuw += 1
        elif not metadata_unchanged:
            summary.gewijzigd += 1
        summary.items.append(
            ScanItem(
                pad=str(path),
                categorie=category,
                filter_reden="bestand veranderde tijdens analyse",
                actie="overslaan",
                besluit_reden="wacht tot kopie of download klaar is",
                nlink=after.st_nlink,
            )
        )
        return

    decision = decide_catalog_item(str(path), category, probe_data, profile, stat.st_nlink)
    summary.gevonden += 1
    summary.items.append(
        ScanItem(
            pad=str(path),
            categorie=category,
            filter_reden=filter_reason,
            actie=decision.actie,
            besluit_reden=decision.reden,
            nlink=stat.st_nlink,
        )
    )
    if dry_run:
        return
    probe_json = json.dumps(probe_data, ensure_ascii=False) if probe_data is not None else None
    changed = bool(
        existing is None
        or existing["scan_status"] != "gereed"
        or not (metadata_unchanged and profile_unchanged)
    )
    now = datetime.now(UTC).isoformat()
    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO bestand(
                library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op,
                device,inode,aanwezig,eerste_gezien,stabiel_vanaf,scan_status,
                profiel_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'gereed', ?)
            """,
            (
                library_id,
                str(path),
                stat.st_size,
                stat.st_mtime,
                stat.st_nlink,
                category,
                probe_json,
                scan_marker,
                stat.st_dev,
                stat.st_ino,
                now,
                now,
                profile_fingerprint,
            ),
        )
        bestand_id = int(cursor.lastrowid)
        summary.nieuw += 1
    else:
        bestand_id = int(existing["id"])
        connection.execute(
            """
            UPDATE bestand
            SET grootte=?,mtime=?,nlink=?,categorie=?,probe=?,gescand_op=?,device=?,inode=?,
                aanwezig=1,stabiel_vanaf=?,scan_status='gereed',profiel_fingerprint=?
            WHERE id=?
            """,
            (
                stat.st_size,
                stat.st_mtime,
                stat.st_nlink,
                category,
                probe_json,
                scan_marker,
                stat.st_dev,
                stat.st_ino,
                now,
                profile_fingerprint,
                bestand_id,
            ),
        )
        summary.gewijzigd += int(changed)
    store_decision(connection, bestand_id, decision, changed)


def _store_deferred_file(
    connection: Any,
    library_id: int,
    path: Path,
    stat: os.stat_result,
    existing: Any | None,
    profile_fingerprint: str,
    scan_marker: str,
    dry_run: bool,
) -> None:
    """Bewaar alleen observaties; een onstabiel bestand krijgt nooit een besluit."""

    if dry_run:
        return
    now = datetime.now(UTC).isoformat()
    if existing is None:
        connection.execute(
            """
            INSERT INTO bestand(
                library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op,
                device,inode,aanwezig,eerste_gezien,scan_status,profiel_fingerprint
            ) VALUES (?, ?, ?, ?, ?, 'wacht', NULL, ?, ?, ?, 1, ?,
                      'wacht_op_stabiliteit', ?)
            """,
            (
                library_id,
                str(path),
                stat.st_size,
                stat.st_mtime,
                stat.st_nlink,
                scan_marker,
                stat.st_dev,
                stat.st_ino,
                now,
                profile_fingerprint,
            ),
        )
        return
    bestand_id = int(existing["id"])
    connection.execute(
        """
        UPDATE bestand
        SET grootte=?,mtime=?,nlink=?,device=?,inode=?,gescand_op=?,aanwezig=1,
            stabiel_vanaf=NULL,scan_status='wacht_op_stabiliteit',profiel_fingerprint=?
        WHERE id=?
        """,
        (
            stat.st_size,
            stat.st_mtime,
            stat.st_nlink,
            stat.st_dev,
            stat.st_ino,
            scan_marker,
            profile_fingerprint,
            bestand_id,
        ),
    )
    connection.execute(
        """
        UPDATE besluit
        SET status='afgewezen',reden=reden || '; bron veranderde en wacht op stabiliteit'
        WHERE bestand_id=? AND status='nieuw'
        """,
        (bestand_id,),
    )
