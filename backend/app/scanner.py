"""Hervatbare, uitsluitend lezende bibliotheekscanner."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.beslisser import decide_catalog_item, store_decision
from app.db import Database
from app.media.bestandsfilter import VIDEO_EXT, is_hoofdbestand
from app.media.probe import probe


class ScanError(RuntimeError):
    """Een library of profiel is ongeldig."""


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

    library, profile = _library(database, library_id)
    root = Path(str(library["pad"]))
    if not root.is_dir():
        raise ScanError(f"Librarypad bestaat niet of is geen map: {root}")

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
            scan_id = database.execute_insert(
                "INSERT INTO scan_run(library_id,status,gestart_op) VALUES (?, 'bezig', ?)",
                (library_id, started_at),
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
                "DELETE FROM bestand WHERE library_id=? AND gescand_op < ?",
                (library_id, started_at),
            )
            summary.verwijderd = cursor.rowcount
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE scan_run SET status='klaar', klaar_op=?, gevonden=? WHERE id=?",
                (now, summary.gevonden, scan_id),
            )
            connection.execute(
                "UPDATE library SET laatste_scan=?, scan_cursor=NULL WHERE id=?",
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
    scan_marker: str,
    dry_run: bool,
    summary: ScanSummary,
) -> None:
    ok, filter_reason, category = is_hoofdbestand(str(path), float(profile.get("min_duur_sec", 0)))
    del ok
    existing = connection.execute(
        "SELECT id,grootte,mtime,nlink FROM bestand WHERE pad=?", (str(path),)
    ).fetchone()
    changed = (
        existing is None
        or int(existing["grootte"]) != stat.st_size
        or float(existing["mtime"]) != stat.st_mtime
        or int(existing["nlink"]) != stat.st_nlink
    )
    probe_data = probe(str(path)) if category in {"ok", "extra"} else None
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
    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO bestand(library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        bestand_id = int(cursor.lastrowid)
        summary.nieuw += 1
    else:
        bestand_id = int(existing["id"])
        connection.execute(
            """
            UPDATE bestand SET grootte=?,mtime=?,nlink=?,categorie=?,probe=?,gescand_op=?
            WHERE id=?
            """,
            (
                stat.st_size,
                stat.st_mtime,
                stat.st_nlink,
                category,
                probe_json,
                scan_marker,
                bestand_id,
            ),
        )
        summary.gewijzigd += int(changed)
    store_decision(connection, bestand_id, decision, changed)
