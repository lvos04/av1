"""Kleine SQLite-laag met genummerde, herhaalbare migraties."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings

LOGGER = logging.getLogger(__name__)
MIGRATION_NAME = re.compile(r"^(?P<version>\d{3})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    """Een migratiebestand is ongeldig of kon niet atomisch worden toegepast."""


class Database:
    """Eén SQLite-database; iedere verbinding zet dezelfde veiligheids-PRAGMA's."""

    def __init__(self, settings: Settings, migrations_dir: Path | None = None) -> None:
        self.settings = settings
        self.path = settings.database_path
        self.migrations_dir = migrations_dir or Path(__file__).with_name("migrations")
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_versie ("
                "versie INTEGER PRIMARY KEY, naam TEXT NOT NULL, toegepast_op TEXT NOT NULL)"
            )
            connection.commit()
        self.apply_migrations()

    def migration_files(self) -> list[tuple[int, Path]]:
        migrations: list[tuple[int, Path]] = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = MIGRATION_NAME.match(path.name)
            if match is None:
                raise MigrationError(f"Ongeldige migratienaam: {path.name}")
            migrations.append((int(match.group("version")), path))
        versions = [version for version, _ in migrations]
        if len(versions) != len(set(versions)):
            raise MigrationError("Dubbele migratieversie gevonden")
        return migrations

    def applied_versions(self) -> set[int]:
        with self.connection() as connection:
            rows = connection.execute("SELECT versie FROM schema_versie").fetchall()
        return {int(row["versie"]) for row in rows}

    def _backup_before_migration(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        backup_dir = self.settings.backup_root / "voor-migratie"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = backup_dir / f"app-{timestamp}.db"
        # SQLite backup voorkomt een inconsistente kopie terwijl WAL actief is.
        with self.connection() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        target.chmod(0o640)

    def apply_migrations(self) -> None:
        with self._write_lock:
            applied = self.applied_versions()
            pending = [
                (version, path)
                for version, path in self.migration_files()
                if version not in applied
            ]
            if pending:
                self._backup_before_migration()
            for version, path in pending:
                sql = path.read_text(encoding="utf-8")
                applied_at = datetime.now(UTC).isoformat()
                quoted_name = path.name.replace("'", "''")
                quoted_time = applied_at.replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    f"{sql}\n"
                    "INSERT INTO schema_versie(versie, naam, toegepast_op) "
                    f"VALUES ({version}, '{quoted_name}', '{quoted_time}');\n"
                    "COMMIT;"
                )
                with self.connection() as connection:
                    try:
                        connection.executescript(script)
                    except sqlite3.Error as exc:
                        with suppress(sqlite3.Error):
                            connection.execute("ROLLBACK")
                        raise MigrationError(f"Migratie {path.name} mislukt: {exc}") from exc
                LOGGER.info("Migratie %s toegepast", path.name)

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        with self._write_lock, self.connection() as connection:
            cursor = connection.execute(sql, parameters)
            connection.commit()
            return cursor.rowcount

    def execute_insert(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        with self._write_lock, self.connection() as connection:
            cursor = connection.execute(sql, parameters)
            connection.commit()
            if cursor.lastrowid is None:
                raise sqlite3.DatabaseError("INSERT leverde geen id op")
            return int(cursor.lastrowid)

    def fetch_one(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self.connection() as connection:
            row: sqlite3.Row | None = connection.execute(sql, parameters).fetchone()
            return row

    def fetch_all(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return list(connection.execute(sql, parameters).fetchall())

    def reachable(self) -> bool:
        try:
            with self.connection() as connection:
                return int(connection.execute("SELECT 1").fetchone()[0]) == 1
        except sqlite3.Error:
            return False
