from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.opslag import (
    StorageChangeError,
    build_storage_candidate,
    persist_storage,
    prepare_storage,
    transient_storage_is_empty,
)


def _system_case(tmp_path: Path) -> Settings:
    pipeline = tmp_path / "pipeline"
    library = tmp_path / "library"
    pipeline.mkdir()
    library.mkdir()
    roots = {name: pipeline / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    return replace(
        settings,
        library_root=library,
        pipeline_root=pipeline,
        workspace_root=pipeline,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
        storage_config_path=roots["config"] / "opslag.json",
        storage_mode="systeemschijf",
        backup_location="systeemschijf",
        system_disk_reserve_gb=150,
        system_device=pipeline.stat().st_dev,
    )


def test_systeemschijfmodus_accepteert_map_en_houdt_harde_bodem(tmp_path: Path) -> None:
    configured = _system_case(tmp_path)

    configured.validate_storage()

    assert configured.effective_cache_limits(1, 1) == (151, 150)
    assert configured.hard_free_floor_bytes == 150 * 1024**3


def test_aparte_schijf_moet_een_echt_afzonderlijk_mountpoint_zijn(tmp_path: Path) -> None:
    configured = _system_case(tmp_path)
    workspace = tmp_path / "latere-schijf"
    workspace.mkdir()
    candidate = build_storage_candidate(
        configured,
        storage_mode="aparte_schijf",
        workspace_path=str(workspace),
        backup_location="werkruimte",
        system_disk_reserve_gb=150,
    )

    with pytest.raises(StorageChangeError, match="afzonderlijk gemount filesystem"):
        prepare_storage(candidate, create=False)


def test_cache_moet_leeg_zijn_voor_padwissel(tmp_path: Path) -> None:
    configured = _system_case(tmp_path)
    assert transient_storage_is_empty(configured)

    (configured.cache_root / "half-resultaat.mkv.deel").write_bytes(b"niet verwijderen")

    assert not transient_storage_is_empty(configured)
    assert (configured.cache_root / "half-resultaat.mkv.deel").read_bytes() == b"niet verwijderen"


def test_atomische_opslagkeuze_wordt_bij_volgende_start_teruggelezen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = _system_case(tmp_path)
    candidate = configured.with_storage(
        storage_mode="systeemschijf",
        workspace_root=configured.pipeline_root,
        backup_location="werkruimte",
        system_disk_reserve_gb=200,
    )
    persist_storage(configured, candidate)
    monkeypatch.setenv("PIPELINE_ROOT", str(configured.pipeline_root))
    monkeypatch.setenv("CONFIG_ROOT", str(configured.config_root))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{configured.database_path}")
    monkeypatch.setenv("LIBRARY_ROOT", str(configured.library_root))

    loaded = Settings.from_env()

    assert loaded.storage_mode == "systeemschijf"
    assert loaded.backup_location == "werkruimte"
    assert loaded.system_disk_reserve_gb == 200
    assert loaded.cache_root == configured.pipeline_root / "cache"
    assert loaded.storage_config_path.read_text(encoding="utf-8").endswith("}\n")
