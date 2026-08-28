"""Veilige, via de UI gekozen opslagindeling zonder mount- of formatteeracties."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.config import BackupLocation, ConfigError, Settings, StorageMode


class StorageChangeError(RuntimeError):
    """Een opslagkeuze kan niet zonder risico worden toegepast."""


def storage_validation_error(settings: Settings) -> str | None:
    """Lees de actuele mounttoestand; een startcontrole alleen is onvoldoende."""

    try:
        settings.validate_storage()
    except ConfigError as exc:
        return str(exc)
    return None


def build_storage_candidate(
    settings: Settings,
    *,
    storage_mode: StorageMode,
    workspace_path: str,
    backup_location: BackupLocation,
    system_disk_reserve_gb: int,
) -> Settings:
    """Leid alle beheerde paden af uit één gekozen werkruimte."""

    if system_disk_reserve_gb < 150:
        raise StorageChangeError("De vaste systeemschijfreserve moet minimaal 150 GB zijn")
    workspace = settings.pipeline_root if storage_mode == "systeemschijf" else Path(workspace_path)
    if not workspace.is_absolute():
        raise StorageChangeError("Het werkruimtepad moet absoluut zijn")
    if workspace in {Path("/"), settings.library_root}:
        raise StorageChangeError("De werkruimte mag nooit / of de library-root zijn")
    return settings.with_storage(
        storage_mode=storage_mode,
        workspace_root=workspace,
        backup_location=backup_location,
        system_disk_reserve_gb=system_disk_reserve_gb,
    )


def _managed_directories(candidate: Settings) -> tuple[Path, ...]:
    values = (candidate.cache_root, candidate.work_root, candidate.backup_root)
    return tuple(dict.fromkeys(values))


def prepare_storage(candidate: Settings, *, create: bool) -> list[str]:
    """Controleer een mount en maak desgevraagd uitsluitend afgeleide submappen."""

    workspace = candidate.workspace_root
    try:
        resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise StorageChangeError(f"Werkruimte is niet toegankelijk: {exc}") from exc
    if not workspace.is_dir() or resolved != workspace:
        raise StorageChangeError("Werkruimte moet een bestaande echte map zonder symlink zijn")
    if candidate.storage_mode == "systeemschijf":
        if (
            workspace.stat().st_dev != candidate.system_device
            or workspace != candidate.pipeline_root
        ):
            raise StorageChangeError("Systeemschijfmodus vereist /srv/av1-pipeline op de rootdisk")
    elif not workspace.is_mount() or workspace.stat().st_dev == candidate.system_device:
        raise StorageChangeError(
            "Het gekozen pad is geen afzonderlijk gemount filesystem; mount de schijf eerst"
        )

    missing = [path for path in _managed_directories(candidate) if not path.exists()]
    if create:
        for path in missing:
            try:
                path.mkdir(mode=0o750, parents=False)
            except OSError as exc:
                raise StorageChangeError(
                    f"Kan opslagmap niet maken: {path}; geef av1pipeline schrijfrecht op de mount"
                ) from exc
    try:
        candidate.validate_storage()
    except ConfigError as exc:
        if not create and missing:
            parents_writable = all(
                path.parent.is_dir() and os.access(path.parent, os.W_OK | os.X_OK)
                for path in missing
            )
            if parents_writable:
                return [str(path) for path in missing]
        raise StorageChangeError(str(exc)) from exc
    return [str(path) for path in missing]


def directory_usage(path: Path) -> dict[str, int]:
    """Tel zonder symlinks te volgen wat een opslagwissel zou achterlaten."""

    files = 0
    directories = 0
    size = 0
    if not path.is_dir():
        return {"bestanden": 0, "mappen": 0, "bytes": 0}
    for root, names, filenames in os.walk(path, followlinks=False):
        directories += len(names)
        for name in filenames:
            item = Path(root) / name
            files += 1
            try:
                size += item.lstat().st_size
            except OSError:
                continue
    return {"bestanden": files, "mappen": directories, "bytes": size}


def storage_report(settings: Settings) -> dict[str, Any]:
    """Geef de UI voldoende bewijs om een opslagkeuze te beoordelen."""

    workspace_exists = settings.workspace_root.is_dir()
    usage = shutil.disk_usage(settings.workspace_root) if workspace_exists else None
    cache = directory_usage(settings.cache_root)
    work = directory_usage(settings.work_root)
    return {
        "modus": settings.storage_mode,
        "pipeline_pad": str(settings.pipeline_root),
        "werkruimte_pad": str(settings.workspace_root),
        "cache_pad": str(settings.cache_root),
        "werk_pad": str(settings.work_root),
        "config_pad": str(settings.config_root),
        "backup_pad": str(settings.backup_root),
        "backups_op": settings.backup_location,
        "systeemschijf_reserve_gb": settings.system_disk_reserve_gb,
        "werkruimte_mountpoint": workspace_exists and settings.workspace_root.is_mount(),
        "werkruimte_op_systeemschijf": (
            workspace_exists and settings.workspace_root.stat().st_dev == settings.system_device
        ),
        "vrij_bytes": usage.free if usage is not None else 0,
        "totaal_bytes": usage.total if usage is not None else 0,
        "cache_inhoud": cache,
        "werk_inhoud": work,
        "kan_wisselen": not any((*cache.values(), *work.values())),
        "uitleg": (
            "Ubuntu, database en werkcache delen dezelfde schijf; de vaste vrije-ruimtebodem "
            "kan niet via de UI worden uitgeschakeld."
            if settings.storage_mode == "systeemschijf"
            else "Cache en werk staan op een afzonderlijke mount; database en configuratie "
            "blijven bereikbaar op de systeemschijf."
        ),
    }


def persist_storage(settings: Settings, candidate: Settings) -> None:
    """Schrijf de volgende startconfiguratie atomisch en duurzaam weg."""

    document = {
        "versie": 1,
        "modus": candidate.storage_mode,
        "werkruimte_pad": str(candidate.workspace_root),
        "backups_op": candidate.backup_location,
        "systeemschijf_reserve_gb": candidate.system_disk_reserve_gb,
    }
    target = settings.storage_config_path
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=".opslag-",
            suffix=".deel",
            delete=False,
        ) as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.chmod(0o640)
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise StorageChangeError(
            f"Opslagconfiguratie kon niet veilig worden opgeslagen: {exc}"
        ) from exc


def transient_storage_is_empty(settings: Settings) -> bool:
    """Een padwissel laat nooit stille, half afgemaakte encodebestanden achter."""

    for path in (settings.cache_root, settings.work_root):
        if path.is_dir() and any(path.iterdir()):
            return False
    return True
