from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from conftest import TESTDATA

from app.config import settings
from app.db import Database
from app.media.beslissen import STANDAARD
from app.scanner import scan_library


def _database(tmp_path: Path) -> Database:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    configured = replace(
        settings,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
    )
    database = Database(configured)
    database.initialize()
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES (?,?)",
        ("Test", json.dumps({**STANDAARD, "drempel_1080": 500})),
    )
    database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id,verwacht_device) VALUES (?,?,?,?)",
        ("Test", str(TESTDATA / "mappen"), profile_id, (TESTDATA / "mappen").stat().st_dev),
    )
    return database


def test_dry_run_schrijft_niets_en_filtert_veilig(tmp_path: Path) -> None:
    database = _database(tmp_path)
    summary = scan_library(database, 1, dry_run=True)
    by_name = {Path(item.pad).name: item for item in summary.items}

    assert by_name["Trailer.Park.Boys.S01E01.1080p.mkv"].categorie == "ok"
    assert by_name["Short.Term.12.2013.1080p.BluRay.x264.mkv"].categorie == "ok"
    assert by_name["Dune Part Two (2024)-trailer.mkv"].categorie == "extra"
    assert by_name["Dune Part Two (2024)-trailer.mkv"].actie == "opruimen"
    assert by_name["Making Of.mkv"].categorie == "extra"
    assert database.fetch_one("SELECT COUNT(*) FROM bestand")[0] == 0


def test_twee_scans_geven_geen_dubbele_rijen(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = scan_library(database, 1)
    second = scan_library(database, 1)

    assert first.nieuw == 6
    assert second.nieuw == 0
    assert database.fetch_one("SELECT COUNT(*) FROM bestand")[0] == 6
    assert database.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 6


def test_afgebroken_scan_wordt_hervat(tmp_path: Path) -> None:
    database = _database(tmp_path)
    partial = scan_library(database, 1, stop_after_directories=2)
    resumed = scan_library(database, 1)

    assert partial.gevonden < 6
    assert resumed.hervat is True
    assert database.fetch_one("SELECT COUNT(*) FROM bestand")[0] == 6
    assert database.fetch_one("SELECT status FROM scan_run ORDER BY id DESC")[0] == "klaar"
