"""SQLite-back-ups met los hashmanifest en herstelcontrole op een tijdelijke kopie."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database


class BackupError(RuntimeError):
    """Een back-up is niet aantoonbaar compleet of herstelbaar."""


def create_backup(database: Database, settings: Settings) -> dict[str, Any]:
    target = settings.backup_root / (
        "app-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".db"
    )
    with database.connection() as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    target.chmod(0o640)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = target.with_suffix(target.suffix + ".sha256")
    manifest.write_text(f"{digest}  {target.name}\n", encoding="ascii")
    manifest.chmod(0o640)
    backup_id = database.execute_insert(
        "INSERT INTO backup_status(pad,grootte,sha256,gemaakt_op) VALUES (?,?,?,?)",
        (str(target), target.stat().st_size, digest, datetime.now(UTC).isoformat()),
    )
    return {"id": backup_id, "pad": str(target), "sha256": digest}


def verify_backup(database: Database, settings: Settings, backup_id: int) -> dict[str, Any]:
    row = database.fetch_one("SELECT * FROM backup_status WHERE id=?", (backup_id,))
    if row is None:
        raise BackupError("Back-up bestaat niet")
    path = Path(str(row["pad"]))
    manifest = path.with_suffix(path.suffix + ".sha256")
    expected_line = f"{row['sha256']}  {path.name}"
    if not path.is_file() or not manifest.is_file():
        raise BackupError("Back-up of hashmanifest ontbreekt")
    if manifest.read_text(encoding="ascii").strip() != expected_line:
        raise BackupError("Hashmanifest wijkt af")
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        raise BackupError("SHA-256 van de back-up wijkt af")
    with tempfile.TemporaryDirectory(dir=settings.work_root, prefix="herstelcontrole-") as name:
        restored = Path(name) / "hersteld.db"
        shutil.copy2(path, restored)
        with sqlite3.connect(restored) as connection:
            check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            connection.execute("SELECT COUNT(*) FROM schema_versie").fetchone()
    if check != "ok":
        raise BackupError(f"SQLite-integriteitscontrole: {check}")
    checked = datetime.now(UTC).isoformat()
    database.execute("UPDATE backup_status SET gecontroleerd_op=? WHERE id=?", (checked, backup_id))
    return {"id": backup_id, "herstelbaar": True, "gecontroleerd_op": checked}


def enforce_retention(database: Database, keep: int = 8) -> int:
    rows = database.fetch_all("SELECT id,pad FROM backup_status ORDER BY gemaakt_op DESC")
    removed = 0
    for row in rows[keep:]:
        path = Path(str(row["pad"]))
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".sha256").unlink(missing_ok=True)
        database.execute("DELETE FROM backup_status WHERE id=?", (int(row["id"]),))
        removed += 1
    return removed
