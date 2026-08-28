"""Configuratie uit het systemd-omgevingsbestand en de procesomgeving."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """De configuratie is onveilig of onvolledig."""


def _absolute_path(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default))
    if not value.is_absolute():
        raise ConfigError(f"{name} moet een absoluut pad zijn: {value}")
    return value


def _integer(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} moet een geheel getal zijn") from exc
    if value < minimum:
        raise ConfigError(f"{name} moet minimaal {minimum} zijn")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} moet een getal zijn") from exc
    if value < minimum:
        raise ConfigError(f"{name} moet minimaal {minimum} zijn")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Niet-geheime procesconfiguratie; bedieningswaarden staan in SQLite."""

    library_root: Path
    pipeline_root: Path
    cache_root: Path
    work_root: Path
    config_root: Path
    backup_root: Path
    database_path: Path
    secret_key_file: Path
    server_name: str
    timezone: str
    log_level: str
    cache_reserve_factor: float
    max_active_jobs: int

    @classmethod
    def from_env(cls) -> Settings:
        pipeline_root = _absolute_path("PIPELINE_ROOT", "/srv/av1-pipeline")
        config_root = _absolute_path("CONFIG_ROOT", str(pipeline_root / "config"))
        database_url = os.environ.get("DATABASE_URL", f"sqlite:///{config_root / 'app.db'}")
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ConfigError("Alleen een lokale SQLite DATABASE_URL is toegestaan")
        database_path = Path(database_url.removeprefix(prefix))
        if not database_path.is_absolute():
            raise ConfigError("DATABASE_URL moet naar een absoluut pad verwijzen")
        return cls(
            library_root=_absolute_path("LIBRARY_ROOT", "/library"),
            pipeline_root=pipeline_root,
            cache_root=_absolute_path("CACHE_ROOT", str(pipeline_root / "cache")),
            work_root=_absolute_path("WORK_ROOT", str(pipeline_root / "work")),
            config_root=config_root,
            backup_root=_absolute_path("BACKUP_ROOT", str(pipeline_root / "backups")),
            database_path=database_path,
            secret_key_file=_absolute_path("SECRET_KEY_FILE", "/etc/av1-pipeline-secrets.key"),
            server_name=os.environ.get("SERVER_NAME", "AV1 Pipeline"),
            timezone=os.environ.get("TIMEZONE", "Europe/Amsterdam"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            cache_reserve_factor=_float("CACHE_RESERVE_FACTOR", 3.0, 1.0),
            max_active_jobs=_integer("MAX_ACTIVE_JOBS", 2, 1),
        )

    @property
    def managed_roots(self) -> tuple[Path, Path, Path, Path]:
        return (self.cache_root, self.work_root, self.config_root, self.backup_root)

    def validate_storage(self) -> None:
        """Weiger een gewone systeemschijfmap waar de project-SSD hoort."""

        if not self.pipeline_root.is_mount():
            raise ConfigError(f"PIPELINE_ROOT is geen echt mountpoint: {self.pipeline_root}")
        expected = {self.pipeline_root / name for name in ("cache", "work", "config", "backups")}
        if set(self.managed_roots) != expected:
            raise ConfigError(
                "Cache-, werk-, config- en back-uppaden moeten direct onder PIPELINE_ROOT staan"
            )
        for path in self.managed_roots:
            if not path.is_dir():
                raise ConfigError(f"Vereiste project-SSD-map ontbreekt: {path}")
            if path.stat().st_uid != os.geteuid():
                raise ConfigError(
                    f"Project-SSD-map heeft niet de backendgebruiker als eigenaar: {path}"
                )
            if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
                raise ConfigError(f"Project-SSD-map is niet lees- en schrijfbaar: {path}")


settings = Settings.from_env()
