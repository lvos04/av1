"""REST-routes voor mutaties en historische gegevens; live data gaat via /ws."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import __version__
from app.backups import BackupError, create_backup
from app.backups import verify_backup as verify_backup_copy
from app.config import Settings
from app.db import Database
from app.geheimen import SecretError, SecretStore
from app.health import dependency_report
from app.kwaliteit import (
    quality_directory,
    run_profile_trial,
    trial_overview,
    verification_overview,
    vmaf_available,
)
from app.media.beslissen import STANDAARD, Besluit, beslis
from app.opslag import (
    StorageChangeError,
    build_storage_candidate,
    persist_storage,
    prepare_storage,
    storage_report,
    storage_validation_error,
    transient_storage_is_empty,
)
from app.scanner import ScanError, scan_library
from app.systeem import GpuMonitor, mount_report, qsv_test, system_metrics
from app.uitvoerder import ExecutionError, create_job_from_decision


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LibraryCreate(StrictModel):
    naam: str = Field(min_length=1, max_length=120)
    pad: str
    profiel_id: int
    scan_interval: int = Field(default=86400, ge=300)


class LibraryPatch(StrictModel):
    naam: str | None = Field(default=None, min_length=1, max_length=120)
    profiel_id: int | None = None
    scan_interval: int | None = Field(default=None, ge=300)
    actief: bool | None = None
    canary_status: Literal["actief", "vrijgegeven"] | None = None


class ProfileCreate(StrictModel):
    naam: str = Field(min_length=1, max_length=120)
    instellingen: dict[str, Any] = Field(default_factory=dict)


class ProfilePatch(StrictModel):
    naam: str | None = Field(default=None, min_length=1, max_length=120)
    instellingen: dict[str, Any] | None = None


class TrialRequest(StrictModel):
    bestand_id: int
    crf_waarden: list[int] = Field(default_factory=lambda: [27, 29, 31], min_length=1)

    @field_validator("crf_waarden")
    @classmethod
    def validate_crf(cls, values: list[int]) -> list[int]:
        if len(values) > 5 or any(value < 0 or value > 63 for value in values):
            raise ValueError("Gebruik maximaal vijf CRF-waarden tussen 0 en 63")
        return values


class DecisionFilter(StrictModel):
    status: str | None = None
    actie: str | None = None
    baan: str | None = None
    encoder: str | None = None
    reden: str | None = None
    library_id: int | None = None


class DecisionBatch(StrictModel):
    ids: list[int] | None = None
    filter: DecisionFilter | None = None
    bevestig_aantal: int | None = None
    bevestig_bytes: int | None = None
    hardlinks_bevestigd: bool = False


class DecisionOverride(StrictModel):
    baan: Literal["cpu", "gpu"]
    encoder: Literal["libsvtav1", "av1_qsv"]
    hardlink_bevestigd: bool = False


class JobPatch(StrictModel):
    prioriteit: int | None = Field(default=None, ge=-1000, le=1000)
    baan: Literal["cpu", "gpu"] | None = None


class WorkerPatch(StrictModel):
    aantal: int | None = Field(default=None, ge=0, le=4)
    nice: int | None = Field(default=None, ge=-20, le=19)
    stilte_start: str | None = None
    stilte_einde: str | None = None


class SettingsPatch(StrictModel):
    server_naam: str | None = Field(default=None, min_length=1, max_length=120)
    tijdzone: str | None = Field(default=None, min_length=1, max_length=100)
    log_niveau: Literal["debug", "info", "warning", "error"] | None = None
    cpu_max_percent: int | None = Field(default=None, ge=10, le=100)
    cpu_max_temperatuur: int | None = Field(default=None, ge=40, le=110)
    cache_waarschuwing_gb: int | None = Field(default=None, ge=1)
    cache_pauze_gb: int | None = Field(default=None, ge=1)
    hardlink_modus: Literal["overslaan", "verbreek_na_bevestiging"] | None = None
    meld_browser_fouten: bool | None = None
    meld_browser_batches: bool | None = None
    pauzeer_cpu_bij_kijken: bool | None = None


class StorageChoice(StrictModel):
    modus: Literal["systeemschijf", "aparte_schijf"]
    werkruimte_pad: str
    backups_op: Literal["systeemschijf", "werkruimte"]
    systeemschijf_reserve_gb: int = Field(ge=150, le=500)


class StorageApply(StorageChoice):
    bevestiging: str


class FeaturePatch(StrictModel):
    ingeschakeld: bool
    configuratie: dict[str, Any] = Field(default_factory=dict)
    bevestiging: str | None = None


class IntegrationTest(StrictModel):
    url: str | None = None
    api_sleutel: str | None = Field(default=None, min_length=1)


class IntegrationConfig(StrictModel):
    url: str | None = None
    api_sleutel: str | None = Field(default=None, min_length=1)
    verwijder_sleutel: bool = False


def _database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _gpu(request: Request) -> GpuMonitor:
    return cast(GpuMonitor, request.app.state.gpu_monitor)


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _profile_settings(value: dict[str, Any]) -> dict[str, Any]:
    unknown = set(value) - set(STANDAARD)
    if unknown:
        raise HTTPException(422, f"Onbekende profielinstellingen: {', '.join(sorted(unknown))}")
    merged = {**STANDAARD, **value}
    if merged["dv_modus"] not in {"skip", "av1"}:
        raise HTTPException(422, "dv_modus moet 'skip' of 'av1' zijn")
    if not isinstance(merged["audio_talen"], list) or not isinstance(
        merged["ondertitel_talen"], list
    ):
        raise HTTPException(422, "taalvoorkeuren moeten lijsten zijn")
    return merged


def _safe_library_path(settings: Settings, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise HTTPException(422, "Librarypad moet absoluut zijn")
    try:
        resolved = path.resolve(strict=True)
        root = settings.library_root.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(422, f"Librarypad is niet toegankelijk: {exc}") from exc
    if not resolved.is_dir() or not resolved.is_relative_to(root):
        raise HTTPException(422, f"Library moet een map binnen {root} zijn")
    return resolved


def _get_row(database: Database, table: str, row_id: int) -> Any:
    allowed = {"library", "profiel", "besluit", "job", "backup_status"}
    if table not in allowed:
        raise RuntimeError("Ongeldige interne tabelnaam")
    row = database.fetch_one(f"SELECT * FROM {table} WHERE id=?", (row_id,))
    if row is None:
        raise HTTPException(404, f"{table.capitalize()} {row_id} bestaat niet")
    return row


def _decision_selection(database: Database, body: DecisionBatch) -> list[Any]:
    if bool(body.ids) == bool(body.filter):
        raise HTTPException(422, "Geef precies ids of filter op")
    query = (
        "SELECT b.*,f.grootte,f.nlink,f.library_id,f.probe "
        "FROM besluit b JOIN bestand f ON f.id=b.bestand_id WHERE 1=1"
    )
    values: list[Any] = []
    if body.ids:
        placeholders = ",".join("?" for _ in body.ids)
        query += f" AND b.id IN ({placeholders})"
        values.extend(body.ids)
    else:
        assert body.filter is not None
        for column, value in (
            ("b.status", body.filter.status),
            ("b.actie", body.filter.actie),
            ("b.baan", body.filter.baan),
            ("b.encoder", body.filter.encoder),
            ("b.reden", body.filter.reden),
            ("f.library_id", body.filter.library_id),
        ):
            if value is not None:
                query += f" AND {column}=?"
                values.append(value)
    query += " ORDER BY b.id"
    return database.fetch_all(query, tuple(values))


def _as_bool_fields(row: dict[str, Any]) -> dict[str, Any]:
    for name in (
        "actief",
        "gepauzeerd",
        "ingeschakeld",
        "meld_browser_fouten",
        "meld_browser_batches",
        "pauzeer_cpu_bij_kijken",
    ):
        if name in row:
            row[name] = bool(row[name])
    return row


def _task_id(request: Request, task: asyncio.Task[Any], identifier: str | None = None) -> str:
    identifier = identifier or str(uuid.uuid4())
    request.app.state.background_tasks[identifier] = task

    def remove(_task: asyncio.Task[Any]) -> None:
        request.app.state.background_tasks.pop(identifier, None)

    task.add_done_callback(remove)
    return identifier


async def _scan_background(database: Database, library_id: int, dry_run: bool) -> None:
    try:
        await asyncio.to_thread(scan_library, database, library_id, dry_run=dry_run)
    except ScanError:
        # De scan_run bevat bij een gewone scan de hervatbare toestand; de UI
        # haalt de concrete fout via diagnose/logging op.
        if not dry_run:
            database.execute(
                "UPDATE scan_run SET status='mislukt',fout=? "
                "WHERE id=(SELECT id FROM scan_run WHERE library_id=? ORDER BY id DESC LIMIT 1)",
                ("scan mislukt; controleer het librarypad", library_id),
            )


def _worker_rows(database: Database) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in database.fetch_all("SELECT * FROM worker_config ORDER BY baan"):
        item = _as_bool_fields(dict(row))
        active = database.fetch_all(
            """
            SELECT j.id AS job_id,j.percent,j.fps,j.speed,j.eta_seconden,j.bitrate_kbps,
                   j.resultaat_pad,f.pad AS bestand,f.grootte AS grootte_bron,b.encoder
            FROM job j JOIN bestand f ON f.id=j.bestand_id
            JOIN besluit b ON b.id=j.besluit_id
            WHERE j.baan=? AND j.status IN ('bezig','verifieren') ORDER BY j.id
            """,
            (row["baan"],),
        )
        item["actieve_jobs"] = [dict(job) for job in active]
        item["historie"] = [
            dict(job)
            for job in database.fetch_all(
                """
                SELECT j.id,j.status,j.gestart_op,j.klaar_op,j.resultaat_grootte,
                       f.pad,f.grootte AS bron_grootte
                FROM job j JOIN bestand f ON f.id=j.bestand_id
                WHERE j.baan=? AND j.status IN ('klaar','mislukt','afgebroken')
                ORDER BY j.klaar_op DESC LIMIT 20
                """,
                (row["baan"],),
            )
        ]
        result.append(item)
    return result


def create_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        database = _database(request)
        dependencies = await dependency_report()
        database_ok = database.reachable()
        request.app.state.storage_error = storage_validation_error(_settings(request))
        return {
            "status": (
                "ok"
                if database_ok
                and dependencies["media_stack_gereed"]
                and request.app.state.storage_error is None
                else "degraded"
            ),
            "versie": __version__,
            "database": "ok" if database_ok else "fout",
            "opslag": (
                "ok" if request.app.state.storage_error is None else request.app.state.storage_error
            ),
            "dependencies": dependencies,
        }

    @router.get("/libraries")
    async def libraries(request: Request) -> list[dict[str, Any]]:
        rows = _database(request).fetch_all(
            """
            SELECT l.*,p.naam AS profiel_naam,COUNT(f.id) AS aantal_bestanden,
                   COALESCE(SUM(f.grootte),0) AS totale_grootte,
                   SUM(CASE WHEN b.status IN ('ingepland','goedgekeurd') OR j.status='klaar'
                            THEN 1 ELSE 0 END) AS verwerkt
            FROM library l JOIN profiel p ON p.id=l.profiel_id
            LEFT JOIN bestand f ON f.library_id=l.id
            LEFT JOIN besluit b ON b.bestand_id=f.id
            LEFT JOIN job j ON j.besluit_id=b.id
            GROUP BY l.id ORDER BY l.naam
            """
        )
        return [_as_bool_fields(dict(row)) for row in rows]

    @router.post("/libraries", status_code=status.HTTP_201_CREATED)
    async def add_library(body: LibraryCreate, request: Request) -> dict[str, Any]:
        database = _database(request)
        _get_row(database, "profiel", body.profiel_id)
        path = _safe_library_path(_settings(request), body.pad)
        try:
            row_id = database.execute_insert(
                """
                INSERT INTO library(naam,pad,profiel_id,scan_interval,verwacht_device)
                VALUES (?,?,?,?,?)
                """,
                (body.naam, str(path), body.profiel_id, body.scan_interval, path.stat().st_dev),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Deze library bestaat al") from exc
        return dict(_get_row(database, "library", row_id))

    @router.patch("/libraries/{library_id}")
    async def update_library(
        library_id: int, body: LibraryPatch, request: Request
    ) -> dict[str, Any]:
        database = _database(request)
        _get_row(database, "library", library_id)
        values = body.model_dump(exclude_none=True)
        if "profiel_id" in values:
            _get_row(database, "profiel", int(values["profiel_id"]))
        if not values:
            return _as_bool_fields(dict(_get_row(database, "library", library_id)))
        assignments = ",".join(f"{key}=?" for key in values)
        database.execute(
            f"UPDATE library SET {assignments} WHERE id=?", (*values.values(), library_id)
        )
        return _as_bool_fields(dict(_get_row(database, "library", library_id)))

    async def start_scan(library_id: int, request: Request, dry_run: bool) -> dict[str, Any]:
        _get_row(_database(request), "library", library_id)
        task = asyncio.create_task(_scan_background(_database(request), library_id, dry_run))
        return {
            "taak_id": _task_id(request, task),
            "library_id": library_id,
            "droogloop": dry_run,
        }

    @router.post("/libraries/{library_id}/scan", status_code=status.HTTP_202_ACCEPTED)
    async def scan(library_id: int, request: Request) -> dict[str, Any]:
        return await start_scan(library_id, request, False)

    @router.post("/libraries/{library_id}/droogloop", status_code=status.HTTP_202_ACCEPTED)
    async def dry_run(library_id: int, request: Request) -> dict[str, Any]:
        return await start_scan(library_id, request, True)

    @router.get("/libraries/{library_id}/suggesties")
    async def library_suggestions(library_id: int, request: Request) -> dict[str, Any]:
        database = _database(request)
        _get_row(database, "library", library_id)
        counts = database.fetch_all(
            """
            SELECT b.actie,b.baan,COUNT(*) AS aantal,COALESCE(SUM(f.grootte),0) AS bytes
            FROM besluit b JOIN bestand f ON f.id=b.bestand_id
            WHERE f.library_id=? GROUP BY b.actie,b.baan ORDER BY aantal DESC
            """,
            (library_id,),
        )
        return {
            "groepen": [dict(row) for row in counts],
            "advies": "Bekijk eerst een droogloop en keur besluiten daarna expliciet goed.",
        }

    @router.get("/profielen")
    async def profiles(request: Request) -> list[dict[str, Any]]:
        return [
            {"id": row["id"], "naam": row["naam"], "instellingen": _json_object(row["inst"])}
            for row in _database(request).fetch_all("SELECT * FROM profiel ORDER BY naam")
        ]

    @router.post("/profielen", status_code=status.HTTP_201_CREATED)
    async def add_profile(body: ProfileCreate, request: Request) -> dict[str, Any]:
        values = _profile_settings(body.instellingen)
        if values["dv_modus"] == "av1":
            feature = _database(request).fetch_one(
                "SELECT ingeschakeld FROM functie WHERE sleutel='dv_experimenteel'"
            )
            if feature is None or not int(feature["ingeschakeld"]):
                raise HTTPException(409, "Schakel experimenteel Dolby Vision eerst expliciet in")
        try:
            row_id = _database(request).execute_insert(
                "INSERT INTO profiel(naam,inst) VALUES (?,?)",
                (body.naam, json.dumps(values, ensure_ascii=False)),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Deze profielnaam bestaat al") from exc
        return {"id": row_id, "naam": body.naam, "instellingen": values}

    @router.patch("/profielen/{profile_id}")
    async def update_profile(
        profile_id: int, body: ProfilePatch, request: Request
    ) -> dict[str, Any]:
        database = _database(request)
        row = _get_row(database, "profiel", profile_id)
        name = body.naam or str(row["naam"])
        values = (
            _profile_settings(body.instellingen)
            if body.instellingen is not None
            else _json_object(str(row["inst"]))
        )
        if values["dv_modus"] == "av1":
            feature = database.fetch_one(
                "SELECT ingeschakeld FROM functie WHERE sleutel='dv_experimenteel'"
            )
            if feature is None or not int(feature["ingeschakeld"]):
                raise HTTPException(409, "Schakel experimenteel Dolby Vision eerst expliciet in")
        database.execute(
            "UPDATE profiel SET naam=?,inst=? WHERE id=?",
            (name, json.dumps(values, ensure_ascii=False), profile_id),
        )
        return {"id": profile_id, "naam": name, "instellingen": values}

    @router.post("/profielen/{profile_id}/proefdraai", status_code=status.HTTP_202_ACCEPTED)
    async def trial(profile_id: int, body: TrialRequest, request: Request) -> dict[str, Any]:
        database = _database(request)
        _get_row(database, "profiel", profile_id)
        file_row = database.fetch_one("SELECT id,pad FROM bestand WHERE id=?", (body.bestand_id,))
        if file_row is None:
            raise HTTPException(404, "Bestand bestaat niet in de catalogus")
        feature = database.fetch_one("SELECT ingeschakeld FROM functie WHERE sleutel='proefdraai'")
        if feature is None or not int(feature["ingeschakeld"]):
            raise HTTPException(409, "Schakel de optionele functie proefdraai eerst in")
        identifier = str(uuid.uuid4())
        task = asyncio.create_task(
            run_profile_trial(
                database,
                _settings(request),
                identifier,
                profile_id,
                body.bestand_id,
                body.crf_waarden,
            )
        )
        return {
            "taak_id": _task_id(request, task, identifier),
            "status": "ingepland",
            "bestand": file_row["pad"],
            "crf_waarden": body.crf_waarden,
        }

    @router.get("/profielen/{profile_id}/proefdraaien")
    async def trials(profile_id: int, request: Request) -> list[dict[str, Any]]:
        database = _database(request)
        _get_row(database, "profiel", profile_id)
        return trial_overview(database, profile_id)

    @router.get("/besluiten")
    async def decisions(
        request: Request,
        status_filter: str | None = Query(default=None, alias="status"),
        actie: str | None = None,
        library_id: int | None = None,
    ) -> dict[str, Any]:
        query = (
            "SELECT b.actie,b.baan,b.encoder,b.reden,b.status,f.library_id,f.nlink,"
            "COUNT(*) AS aantal,SUM(f.grootte) AS bytes,MIN(b.id) AS eerste_id "
            "FROM besluit b JOIN bestand f ON f.id=b.bestand_id WHERE 1=1"
        )
        values: list[Any] = []
        for column, value in (
            ("b.status", status_filter),
            ("b.actie", actie),
            ("f.library_id", library_id),
        ):
            if value is not None:
                query += f" AND {column}=?"
                values.append(value)
        query += (
            " GROUP BY b.actie,b.baan,b.encoder,b.reden,b.status,f.library_id,f.nlink "
            "ORDER BY eerste_id"
        )
        rows = _database(request).fetch_all(query, tuple(values))
        return {
            "groepen": [dict(row) for row in rows],
            "totaal": sum(row["aantal"] for row in rows),
        }

    @router.get("/besluiten/items")
    async def decision_items(
        request: Request,
        status_filter: str | None = Query(default=None, alias="status"),
        actie: str | None = None,
        baan: str | None = None,
        encoder: str | None = None,
        reden: str | None = None,
        library_id: int | None = None,
        pagina: int = Query(default=1, ge=1),
        per_pagina: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        where = " WHERE 1=1"
        values: list[Any] = []
        for column, value in (
            ("b.status", status_filter),
            ("b.actie", actie),
            ("b.baan", baan),
            ("b.encoder", encoder),
            ("b.reden", reden),
            ("f.library_id", library_id),
        ):
            if value is not None:
                where += f" AND {column}=?"
                values.append(value)
        database = _database(request)
        count = database.fetch_one(
            "SELECT COUNT(*) AS aantal FROM besluit b JOIN bestand f ON f.id=b.bestand_id" + where,
            tuple(values),
        )
        rows = database.fetch_all(
            """
            SELECT b.id,b.bestand_id,b.actie,b.baan,b.encoder,b.reden,b.status,b.door_mens,
                   f.pad,f.grootte,f.nlink,f.library_id,l.naam AS library_naam
            FROM besluit b JOIN bestand f ON f.id=b.bestand_id
            JOIN library l ON l.id=f.library_id
            """
            + where
            + " ORDER BY b.id LIMIT ? OFFSET ?",
            (*values, per_pagina, (pagina - 1) * per_pagina),
        )
        return {
            "items": [dict(row) for row in rows],
            "totaal": int(count["aantal"]) if count is not None else 0,
            "pagina": pagina,
        }

    @router.post("/besluiten/goedkeuren")
    async def approve(body: DecisionBatch, request: Request) -> dict[str, Any]:
        database = _database(request)
        rows = _decision_selection(database, body)
        if not rows:
            raise HTTPException(404, "Geen besluiten gevonden")
        eligible = [row for row in rows if row["status"] in {"nieuw", "afgewezen"}]
        cleanup = [row for row in eligible if row["actie"] == "opruimen"]
        if cleanup:
            total_bytes = sum(int(row["grootte"]) for row in cleanup)
            if body.bevestig_aantal != len(cleanup) or body.bevestig_bytes != total_bytes:
                raise HTTPException(
                    409,
                    {
                        "melding": (
                            "Permanent opruimen vereist exact aantal en bytes als bevestiging"
                        ),
                        "aantal": len(cleanup),
                        "bytes": total_bytes,
                    },
                )
        hardlinks = [row for row in eligible if int(row["nlink"]) > 1]
        if hardlinks:
            feature = database.fetch_one(
                "SELECT ingeschakeld FROM functie WHERE sleutel='hardlink_verbreken'"
            )
            setting = database.fetch_one("SELECT hardlink_modus FROM instelling WHERE id=1")
            if (
                feature is None
                or not int(feature["ingeschakeld"])
                or setting is None
                or setting["hardlink_modus"] != "verbreek_na_bevestiging"
                or not body.hardlinks_bevestigd
                or any(not int(row["hardlink_bevestigd"]) for row in hardlinks)
            ):
                raise HTTPException(
                    409,
                    "Hardlinks vereisen beleid, functie, bevestiging per bestand "
                    "en eindbevestiging",
                )
        job_ids: list[int] = []
        for row in eligible:
            decision_id = int(row["id"])
            database.execute(
                "UPDATE besluit SET status='goedgekeurd',door_mens=1,"
                "hardlink_bevestigd=? WHERE id=?",
                (
                    int(int(row["nlink"]) > 1 and body.hardlinks_bevestigd),
                    decision_id,
                ),
            )
            if row["actie"] in {"hercoderen", "opschonen"}:
                try:
                    job_ids.append(create_job_from_decision(database, decision_id))
                except ExecutionError as exc:
                    raise HTTPException(409, str(exc)) from exc
        return {"goedgekeurd": len(eligible), "job_ids": job_ids}

    @router.post("/besluiten/afwijzen")
    async def reject(body: DecisionBatch, request: Request) -> dict[str, int]:
        rows = _decision_selection(_database(request), body)
        changed = 0
        for row in rows:
            if row["status"] == "nieuw":
                changed += _database(request).execute(
                    "UPDATE besluit SET status='afgewezen',door_mens=1 "
                    "WHERE id=? AND status='nieuw'",
                    (int(row["id"]),),
                )
        return {"afgewezen": changed}

    @router.patch("/besluiten/{decision_id}")
    async def override_decision(
        decision_id: int, body: DecisionOverride, request: Request
    ) -> dict[str, Any]:
        if (body.baan, body.encoder) not in {
            ("cpu", "libsvtav1"),
            ("gpu", "av1_qsv"),
        }:
            raise HTTPException(422, "Baan en encoder passen niet bij elkaar")
        database = _database(request)
        row = database.fetch_one(
            """
            SELECT b.*,f.probe,f.nlink,f.grootte,f.categorie,l.profiel_id,p.inst AS profiel_inst
            FROM besluit b JOIN bestand f ON f.id=b.bestand_id
            JOIN library l ON l.id=f.library_id JOIN profiel p ON p.id=l.profiel_id
            WHERE b.id=?
            """,
            (decision_id,),
        )
        if row is None:
            raise HTTPException(404, "Besluit bestaat niet")
        probe = _json_object(str(row["probe"] or "{}"))
        if probe.get("hdr10plus"):
            raise HTTPException(409, "HDR10+ kan nooit geforceerd worden")
        hardlink = int(row["nlink"]) > 1
        if hardlink:
            feature = database.fetch_one(
                "SELECT ingeschakeld FROM functie WHERE sleutel='hardlink_verbreken'"
            )
            setting = database.fetch_one("SELECT hardlink_modus FROM instelling WHERE id=1")
            if (
                not body.hardlink_bevestigd
                or feature is None
                or not int(feature["ingeschakeld"])
                or setting is None
                or setting["hardlink_modus"] != "verbreek_na_bevestiging"
            ):
                raise HTTPException(
                    409,
                    "Bevestig per bestand dat de torrent-hardlink oud blijft "
                    "en niet wordt hersteld",
                )
            profile = _json_object(str(row["profiel_inst"]))
            payload = beslis(probe, {**STANDAARD, **profile})
            if payload.actie not in {"hercoderen", "opschonen"}:
                raise HTTPException(
                    409, f"Ook zonder hardlink blijft dit bestand veilig overslaan: {payload.reden}"
                )
            payload.waarschuwingen.append(
                "hardlink wordt bewust verbroken; blijvende extra ruimte kan "
                "gelijk zijn aan de brongrootte"
            )
            payload.reden += "; hardlink verbreken na dubbele bevestiging"
        else:
            payload = Besluit.model_validate_json(str(row["payload"]))
        payload.baan = body.baan
        payload.encoder = body.encoder
        payload.herstel_hdr_na = bool(body.encoder == "av1_qsv" and probe.get("is_hdr10"))
        changed = database.execute(
            "UPDATE besluit SET actie=?,baan=?,encoder=?,reden=?,payload=?,door_mens=1,"
            "hardlink_bevestigd=? "
            "WHERE id=? AND status='nieuw'",
            (
                payload.actie,
                body.baan,
                body.encoder,
                payload.reden,
                payload.model_dump_json(),
                int(hardlink and body.hardlink_bevestigd),
                decision_id,
            ),
        )
        if changed != 1:
            raise HTTPException(409, "Alleen een nieuw besluit kan worden overruled")
        return {
            "id": decision_id,
            "actie": payload.actie,
            "baan": body.baan,
            "encoder": body.encoder,
            "hardlink_bevestigd": hardlink and body.hardlink_bevestigd,
            "verwachte_extra_bytes": int(row["grootte"]) if hardlink else 0,
        }

    @router.get("/jobs")
    async def jobs(
        request: Request,
        status_filter: str | None = Query(default=None, alias="status"),
        baan: str | None = None,
        encoder: str | None = None,
        library_id: int | None = None,
        pagina: int = Query(default=1, ge=1),
        per_pagina: int = Query(default=50, ge=1, le=250),
    ) -> dict[str, Any]:
        where = " WHERE 1=1"
        values: list[Any] = []
        for column, value in (
            ("j.status", status_filter),
            ("j.baan", baan),
            ("b.encoder", encoder),
            ("f.library_id", library_id),
        ):
            if value is not None:
                where += f" AND {column}=?"
                values.append(value)
        database = _database(request)
        count = database.fetch_one(
            "SELECT COUNT(*) AS aantal FROM job j JOIN bestand f ON f.id=j.bestand_id "
            "JOIN besluit b ON b.id=j.besluit_id" + where,
            tuple(values),
        )
        rows = database.fetch_all(
            """
            SELECT j.*,f.pad,f.grootte AS bron_grootte,f.library_id,b.actie,b.encoder,b.reden,
                   v.geslaagd AS verificatie_geslaagd,v.toelichting AS verificatie_toelichting
            FROM job j JOIN bestand f ON f.id=j.bestand_id
            JOIN besluit b ON b.id=j.besluit_id
            LEFT JOIN verificatie v ON v.job_id=j.id
            """
            + where
            + " ORDER BY j.prioriteit DESC,j.id LIMIT ? OFFSET ?",
            (*values, per_pagina, (pagina - 1) * per_pagina),
        )
        return {
            "items": [dict(row) for row in rows],
            "totaal": int(count["aantal"]) if count is not None else 0,
            "pagina": pagina,
            "per_pagina": per_pagina,
        }

    @router.post("/jobs/{job_id}/afbreken")
    async def abort_job(job_id: int, request: Request) -> dict[str, Any]:
        database = _database(request)
        row = _get_row(database, "job", job_id)
        if row["status"] not in {"wacht", "bezig", "verifieren"}:
            raise HTTPException(409, "Alleen wachtende of actieve jobs kunnen worden afgebroken")
        if row["status"] == "wacht":
            database.execute(
                "UPDATE job SET status='afgebroken',fout='door gebruiker afgebroken',"
                "klaar_op=? WHERE id=?",
                (datetime.now(UTC).isoformat(), job_id),
            )
        else:
            database.execute("UPDATE job SET afbreken_gevraagd=1 WHERE id=?", (job_id,))
        return {"id": job_id, "afbreken_gevraagd": True}

    @router.post("/jobs/{job_id}/opnieuw")
    async def retry_job(job_id: int, request: Request) -> dict[str, Any]:
        database = _database(request)
        row = _get_row(database, "job", job_id)
        if row["status"] not in {"mislukt", "afgebroken", "overgeslagen"}:
            raise HTTPException(409, "Deze job kan niet opnieuw worden gestart")
        if int(row["zelfde_fout_pogingen"] or 0) >= 3:
            raise HTTPException(
                409,
                "Deze job faalde drie keer identiek; wijzig eerst bron, profiel of oorzaak",
            )
        database.execute(
            """
            UPDATE job SET status='wacht',fout=NULL,foutsoort=NULL,afbreken_gevraagd=0,
                           percent=0,proces_pid=NULL,worker_id=NULL,klaar_op=NULL WHERE id=?
            """,
            (job_id,),
        )
        return {"id": job_id, "status": "wacht"}

    @router.patch("/jobs/{job_id}")
    async def update_job(job_id: int, body: JobPatch, request: Request) -> dict[str, Any]:
        database = _database(request)
        row = _get_row(database, "job", job_id)
        if row["status"] != "wacht":
            raise HTTPException(409, "Alleen een wachtende job kan worden gewijzigd")
        values = body.model_dump(exclude_none=True)
        if values:
            assignments = ",".join(f"{key}=?" for key in values)
            database.execute(f"UPDATE job SET {assignments} WHERE id=?", (*values.values(), job_id))
        return dict(_get_row(database, "job", job_id))

    @router.post("/jobs/{job_id}/vmaf", status_code=status.HTTP_202_ACCEPTED)
    async def vmaf(job_id: int, request: Request) -> dict[str, Any]:
        database = _database(request)
        _get_row(database, "job", job_id)
        feature = database.fetch_one("SELECT ingeschakeld FROM functie WHERE sleutel='vmaf'")
        if feature is None or not int(feature["ingeschakeld"]):
            raise HTTPException(409, "Schakel VMAF eerst in")
        if not await vmaf_available():
            raise HTTPException(409, "Deze FFmpeg-build bevat het libvmaf-filter niet")
        raise HTTPException(
            409,
            "VMAF moet vóór de atomische vervanging zijn geactiveerd zodat bron en resultaat "
            "tegelijk beschikbaar zijn",
        )

    @router.get("/workers")
    async def workers(request: Request) -> list[dict[str, Any]]:
        return _worker_rows(_database(request))

    @router.post("/workers/{lane}/pauzeer")
    async def pause_worker(lane: Literal["cpu", "gpu"], request: Request) -> dict[str, Any]:
        database = _database(request)
        row = database.fetch_one("SELECT gepauzeerd FROM worker_config WHERE baan=?", (lane,))
        assert row is not None
        paused = not bool(row["gepauzeerd"])
        database.execute(
            "UPDATE worker_config SET gepauzeerd=?,pauze_reden=? WHERE baan=?",
            (int(paused), "handmatig gepauzeerd" if paused else None, lane),
        )
        return {"baan": lane, "gepauzeerd": paused}

    @router.patch("/workers/{lane}")
    async def update_worker(
        lane: Literal["cpu", "gpu"], body: WorkerPatch, request: Request
    ) -> dict[str, Any]:
        values = body.model_dump(exclude_none=True)
        database = _database(request)
        if values:
            assignments = ",".join(f"{key}=?" for key in values)
            database.execute(
                f"UPDATE worker_config SET {assignments} WHERE baan=?", (*values.values(), lane)
            )
        row = database.fetch_one("SELECT * FROM worker_config WHERE baan=?", (lane,))
        assert row is not None
        return _as_bool_fields(dict(row))

    @router.get("/jobs/{job_id}/log")
    async def job_log(
        job_id: int, request: Request, vanaf: int = Query(default=0, ge=0)
    ) -> dict[str, Any]:
        _get_row(_database(request), "job", job_id)
        path = _settings(request).config_root / "logs" / f"job-{job_id}.log"
        if not path.is_file():
            return {"regels": [], "volgende": vanaf}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[vanaf : vanaf + 1000]
        return {"regels": selected, "volgende": vanaf + len(selected)}

    @router.get("/fouten")
    async def errors(request: Request) -> dict[str, Any]:
        rows = _database(request).fetch_all(
            """
            SELECT COALESCE(j.foutsoort,'onbekend') AS foutsoort,j.fout,COUNT(*) AS aantal,
                   MAX(j.klaar_op) AS laatst
            FROM job j WHERE j.status='mislukt'
            GROUP BY j.foutsoort,j.fout ORDER BY aantal DESC,laatst DESC
            """
        )
        return {"groepen": [dict(row) for row in rows]}

    @router.get("/statistieken")
    async def statistics(request: Request) -> dict[str, Any]:
        database = _database(request)
        daily = database.fetch_all(
            """
            SELECT dag,SUM(bytes_voor-bytes_na) AS bespaard_bytes,COUNT(*) AS jobs
            FROM besparing WHERE dag >= date('now','-29 days') GROUP BY dag ORDER BY dag
            """
        )
        totals = database.fetch_one(
            """
            SELECT COALESCE(SUM(bytes_voor-bytes_na),0) AS bespaard_bytes,
                   COALESCE(AVG((bytes_voor-bytes_na)*100.0/NULLIF(bytes_voor,0)),0) AS gemiddeld,
                   COUNT(*) AS jobs FROM besparing
            """
        )
        last_week = database.fetch_one(
            "SELECT COALESCE(SUM(bytes_voor),0)/7.0 AS bytes_per_dag FROM besparing "
            "WHERE dag >= date('now','-6 days')"
        )
        remaining = database.fetch_one(
            """
            SELECT COALESCE(SUM(f.grootte),0) AS bytes FROM bestand f
            JOIN besluit b ON b.bestand_id=f.id
            WHERE b.status IN ('nieuw','goedgekeurd','ingepland')
              AND b.actie IN ('hercoderen','opschonen')
            """
        )
        rate = float(last_week["bytes_per_dag"]) if last_week is not None else 0.0
        remaining_bytes = int(remaining["bytes"]) if remaining is not None else 0
        days = remaining_bytes / rate if rate > 0 else None
        finish = datetime.now(UTC) + timedelta(days=days) if days is not None else None
        return {
            "totaal": dict(totals) if totals is not None else {},
            "per_dag": [dict(row) for row in daily],
            "doorvoer_bytes_per_dag_7d": rate,
            "resterend_bytes": remaining_bytes,
            "prognose_klaar": finish.isoformat() if finish is not None else None,
        }

    @router.get("/instellingen")
    async def get_settings(request: Request) -> dict[str, Any]:
        row = _database(request).fetch_one(
            """
            SELECT id,server_naam,tijdzone,log_niveau,cpu_max_percent,cpu_max_temperatuur,
                   cache_waarschuwing_gb,cache_pauze_gb,hardlink_modus,
                   meld_browser_fouten,meld_browser_batches,pauzeer_cpu_bij_kijken,
                   jellyfin_url,radarr_url,sonarr_url
            FROM instelling WHERE id=1
            """
        )
        assert row is not None
        return _as_bool_fields(dict(row))

    @router.get("/opslag")
    async def get_storage(request: Request) -> dict[str, Any]:
        database = _database(request)
        request.app.state.storage_error = storage_validation_error(_settings(request))
        result = storage_report(_settings(request))
        active = database.fetch_one(
            "SELECT COUNT(*) AS aantal FROM job WHERE status IN ('bezig','verifieren')"
        )
        result["actieve_jobs"] = int(active["aantal"]) if active is not None else 0
        result["achtergrondtaken"] = len(request.app.state.background_tasks)
        result["opslagfout"] = request.app.state.storage_error
        return result

    def storage_candidate(body: StorageChoice, request: Request) -> Settings:
        try:
            return build_storage_candidate(
                _settings(request),
                storage_mode=body.modus,
                workspace_path=body.werkruimte_pad,
                backup_location=body.backups_op,
                system_disk_reserve_gb=body.systeemschijf_reserve_gb,
            )
        except StorageChangeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/opslag/controle")
    async def check_storage(body: StorageChoice, request: Request) -> dict[str, Any]:
        candidate = storage_candidate(body, request)
        try:
            missing = prepare_storage(candidate, create=False)
            result = storage_report(candidate)
        except (OSError, StorageChangeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        result["te_maken_mappen"] = missing
        result["controle"] = "veilig toepasbaar"
        return result

    @router.patch("/opslag")
    async def update_storage(body: StorageApply, request: Request) -> dict[str, Any]:
        if body.bevestiging != "OPSLAG WIJZIGEN":
            raise HTTPException(409, "Typ exact OPSLAG WIJZIGEN")
        database = _database(request)
        current = _settings(request)
        candidate = storage_candidate(body, request)
        active = database.fetch_one(
            "SELECT COUNT(*) AS aantal FROM job WHERE status IN ('bezig','verifieren')"
        )
        if active is not None and int(active["aantal"]):
            raise HTTPException(409, "Stop actieve jobs voordat je de opslag wijzigt")
        if request.app.state.background_tasks:
            raise HTTPException(409, "Wacht tot scans en kwaliteitsproeven klaar zijn")
        workspace_changes = (
            candidate.cache_root != current.cache_root or candidate.work_root != current.work_root
        )
        if workspace_changes and not transient_storage_is_empty(current):
            raise HTTPException(
                409,
                "Maak de huidige cache en werkmap gecontroleerd leeg voordat je wisselt",
            )
        try:
            prepare_storage(candidate, create=True)
            persist_storage(current, candidate)
        except (OSError, StorageChangeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        old = json.dumps(storage_report(current), ensure_ascii=False)
        database.execute(
            "UPDATE worker_config SET gepauzeerd=1,"
            "pauze_reden='opslag gewijzigd; controleer en hervat'"
        )
        if candidate.storage_mode == "systeemschijf":
            database.execute(
                """
                UPDATE instelling
                SET cache_pauze_gb=MAX(cache_pauze_gb,?),
                    cache_waarschuwing_gb=MAX(cache_waarschuwing_gb,?)
                WHERE id=1
                """,
                (
                    candidate.system_disk_reserve_gb,
                    candidate.system_disk_reserve_gb + 100,
                ),
            )
        current.activate_storage(candidate)
        request.app.state.storage_error = None
        result = storage_report(current)
        database.execute(
            "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) "
            "VALUES ('opslag','werkruimte',?,?,?)",
            (old, json.dumps(result, ensure_ascii=False), datetime.now(UTC).isoformat()),
        )
        result["melding"] = (
            "Opslag actief; beide workers blijven gepauzeerd tot je ze controleert en hervat"
        )
        return result

    @router.patch("/instellingen")
    async def update_settings(body: SettingsPatch, request: Request) -> dict[str, Any]:
        values = body.model_dump(exclude_none=True)
        database = _database(request)
        current = database.fetch_one(
            "SELECT cache_pauze_gb,cache_waarschuwing_gb FROM instelling WHERE id=1"
        )
        assert current is not None
        pause = int(values.get("cache_pauze_gb", current["cache_pauze_gb"]))
        warning = int(values.get("cache_waarschuwing_gb", current["cache_waarschuwing_gb"]))
        app_settings = _settings(request)
        if (
            app_settings.storage_mode == "systeemschijf"
            and pause < app_settings.system_disk_reserve_gb
        ):
            raise HTTPException(
                422,
                "Cachepauze mag niet onder de vaste systeemschijfreserve komen",
            )
        if pause >= warning:
            raise HTTPException(422, "Cachepauze moet lager zijn dan cachewaarschuwing")
        for key in (
            "meld_browser_fouten",
            "meld_browser_batches",
            "pauzeer_cpu_bij_kijken",
        ):
            if key in values:
                values[key] = int(values[key])
        if values:
            assignments = ",".join(f"{key}=?" for key in values)
            database.execute(
                f"UPDATE instelling SET {assignments} WHERE id=1", tuple(values.values())
            )
        return await get_settings(request)

    @router.get("/functies")
    async def features(request: Request) -> list[dict[str, Any]]:
        descriptions = {
            "gpu_monitor": "Optionele intel_gpu_top-meting; encoden blijft onafhankelijk.",
            "vmaf": "Kwaliteitsmeting op een korte steekproef.",
            "proefdraai": "Vergelijk meerdere CRF-waarden op een fragment.",
            "browsermeldingen": "Meld afgeronde batches en fouten in de browser.",
            "jellyfin_pauze": "Pauzeer alleen de CPU-baan tijdens afspelen.",
            "radarr": "Vraag alleen een gerichte rescan na vervanging.",
            "sonarr": "Vraag alleen een gerichte rescan na vervanging.",
            "externe_toegang": "Extra authenticatie voor toegang buiten het eigen netwerk.",
            "hardlink_verbreken": (
                "Verbreek na dubbele bevestiging één hardlink; geen herstelbelofte."
            ),
            "dv_experimenteel": "Sta AV1 met Dolby Vision toe; compatibiliteit blijft beperkt.",
            "automatische_backups": "Maak periodieke gecontroleerde SQLite-back-ups.",
            "canary_limiet": "Beperk een nieuw profiel eerst tot enkele jobs.",
        }
        result = []
        for row in _database(request).fetch_all("SELECT * FROM functie ORDER BY sleutel"):
            item = _as_bool_fields(dict(row))
            item["configuratie"] = _json_object(str(item["configuratie"]))
            item["uitleg"] = descriptions.get(str(item["sleutel"]), "Optionele functie")
            result.append(item)
        return result

    @router.patch("/functies/{key}")
    async def update_feature(key: str, body: FeaturePatch, request: Request) -> dict[str, Any]:
        database = _database(request)
        row = database.fetch_one("SELECT * FROM functie WHERE sleutel=?", (key,))
        if row is None:
            raise HTTPException(404, "Onbekende functie")
        confirmations = {
            "externe_toegang": (True, "LAN-TOEGANG INSCHAKELEN"),
            "hardlink_verbreken": (True, "HARDLINKS VERBREKEN"),
            "dv_experimenteel": (True, "EXPERIMENTEEL DOLBY VISION"),
            "canary_limiet": (False, "CANARY-LIMIET UITZETTEN"),
        }
        risky = confirmations.get(key)
        if risky is not None and body.ingeschakeld == risky[0] and body.bevestiging != risky[1]:
            raise HTTPException(409, f"Typ ter bevestiging exact: {risky[1]}")
        database.execute(
            "UPDATE functie SET ingeschakeld=?,configuratie=?,gewijzigd_op=? WHERE sleutel=?",
            (
                int(body.ingeschakeld),
                json.dumps(body.configuratie, ensure_ascii=False),
                datetime.now(UTC).isoformat(),
                key,
            ),
        )
        database.execute(
            "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) VALUES ('functie',?,?,?,?)",
            (
                key,
                json.dumps({"ingeschakeld": bool(row["ingeschakeld"])}, ensure_ascii=False),
                json.dumps({"ingeschakeld": body.ingeschakeld}, ensure_ascii=False),
                datetime.now(UTC).isoformat(),
            ),
        )
        if key == "gpu_monitor":
            await _gpu(request).restart(body.ingeschakeld)
        return {
            "sleutel": key,
            "ingeschakeld": body.ingeschakeld,
            "configuratie": body.configuratie,
        }

    @router.post("/systeem/qsv-test")
    async def run_qsv_test(request: Request) -> dict[str, Any]:
        result = await qsv_test(_settings(request))
        if not result["geslaagd"]:
            raise HTTPException(503, result)
        return result

    @router.post("/systeem/gpu-herstart")
    async def restart_gpu_monitor(request: Request) -> dict[str, Any]:
        row = _database(request).fetch_one(
            "SELECT ingeschakeld FROM functie WHERE sleutel='gpu_monitor'"
        )
        enabled = bool(row and row["ingeschakeld"])
        return {
            "monitor": await _gpu(request).restart(enabled),
            "driver_ongemoeid": True,
        }

    @router.get("/systeem/diagnose")
    async def diagnostics(request: Request) -> dict[str, Any]:
        return {
            "dependencies": await dependency_report(),
            "systeem": system_metrics(_database(request), _settings(request), _gpu(request)),
            "mounts": mount_report(_settings(request)),
        }

    @router.get("/systeem/mounts")
    async def mounts(request: Request) -> dict[str, Any]:
        return mount_report(_settings(request))

    @router.get("/kwaliteit/status")
    async def quality_status(request: Request) -> dict[str, Any]:
        database = _database(request)
        feature = database.fetch_one("SELECT ingeschakeld FROM functie WHERE sleutel='vmaf'")
        available = await vmaf_available()
        return {
            "vmaf_beschikbaar": available,
            "vmaf_ingeschakeld": bool(feature and feature["ingeschakeld"]),
            "uitleg": (
                "libvmaf is beschikbaar voor steekproeven"
                if available
                else "De geïnstalleerde FFmpeg-build bevat geen libvmaf; verificatie blijft werken"
            ),
        }

    @router.get("/kwaliteit/verificaties")
    async def verifications(request: Request) -> list[dict[str, Any]]:
        return verification_overview(_database(request))

    @router.get("/kwaliteit/jobs/{job_id}/frame/{kind}")
    async def quality_frame(
        job_id: int, kind: Literal["bron", "resultaat"], request: Request
    ) -> FileResponse:
        database = _database(request)
        column = "bron_frame" if kind == "bron" else "resultaat_frame"
        row = database.fetch_one(
            f"SELECT {column} AS pad FROM kwaliteitsmeting WHERE job_id=? ORDER BY id DESC LIMIT 1",
            (job_id,),
        )
        if row is None or not row["pad"]:
            raise HTTPException(404, "Vergelijkingsframe is niet vastgelegd")
        path = Path(str(row["pad"])).resolve()
        expected = quality_directory(_settings(request), job_id).resolve()
        if not path.is_file() or not path.is_relative_to(expected):
            raise HTTPException(404, "Vergelijkingsframe ontbreekt")
        return FileResponse(path, media_type="image/jpeg")

    @router.post("/backups/nu", status_code=status.HTTP_201_CREATED)
    async def backup_now(request: Request) -> dict[str, Any]:
        return create_backup(_database(request), _settings(request))

    @router.get("/backups")
    async def backups(request: Request) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in _database(request).fetch_all(
                "SELECT * FROM backup_status ORDER BY gemaakt_op DESC LIMIT 50"
            )
        ]

    @router.get("/herstel")
    async def recovery_overview(request: Request) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in _database(request).fetch_all(
                """
                SELECT v.*,j.status AS job_status FROM vervang_transactie v
                JOIN job j ON j.id=v.job_id
                WHERE v.status NOT IN ('vervangen','hersteld','opgeruimd')
                   OR j.status NOT IN ('klaar','wacht')
                ORDER BY v.id DESC
                """
            )
        ]

    @router.delete("/herstel/{transaction_id}/tijdelijk-resultaat")
    async def remove_recovery_result(transaction_id: int, request: Request) -> dict[str, Any]:
        database = _database(request)
        row = database.fetch_one("SELECT * FROM vervang_transactie WHERE id=?", (transaction_id,))
        if row is None:
            raise HTTPException(404, "Hersteltransactie bestaat niet")
        if row["status"] != "conflict":
            raise HTTPException(409, "Alleen een conflicterend tijdelijk resultaat is opruimbaar")
        source = Path(str(row["bron_pad"])).resolve()
        temporary = Path(str(row["tijdelijk_pad"])).resolve()
        expected = source.with_name(f".{source.name}.av1-pipeline-{int(row['job_id'])}.deel")
        if temporary != expected or temporary.parent != source.parent:
            raise HTTPException(409, "Tijdelijk pad voldoet niet aan het vervangprotocol")
        if not source.is_file():
            raise HTTPException(
                409, "Bron ontbreekt; verwijder geen mogelijk enig resultaat automatisch"
            )
        temporary.unlink(missing_ok=True)
        database.execute(
            "UPDATE vervang_transactie SET status='opgeruimd',toelichting=?,gewijzigd_op=? "
            "WHERE id=?",
            (
                "tijdelijk resultaat na expliciete gebruikersactie verwijderd; bron bleef intact",
                datetime.now(UTC).isoformat(),
                transaction_id,
            ),
        )
        return {"id": transaction_id, "tijdelijk_verwijderd": True, "bron_ongemoeid": True}

    @router.post("/backups/{backup_id}/controleer-herstel")
    async def verify_backup_endpoint(backup_id: int, request: Request) -> dict[str, Any]:
        try:
            return verify_backup_copy(_database(request), _settings(request), backup_id)
        except BackupError as exc:
            raise HTTPException(409, str(exc)) from exc

    async def integration_test(
        kind: str, body: IntegrationTest, request: Request
    ) -> dict[str, Any]:
        paths = {
            "jellyfin": "/System/Info/Public",
            "radarr": "/api/v3/system/status",
            "sonarr": "/api/v3/system/status",
        }
        database = _database(request)
        setting = database.fetch_one(f"SELECT {kind}_url AS url FROM instelling WHERE id=1")
        url = body.url or (str(setting["url"]) if setting and setting["url"] else "")
        try:
            api_key = body.api_sleutel or SecretStore(database, _settings(request)).get(
                f"{kind}_api_sleutel"
            )
        except SecretError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not url or not api_key:
            raise HTTPException(422, f"{kind} URL en API-sleutel zijn niet ingesteld")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(422, "Integratie-URL moet met http:// of https:// beginnen")
        headers = {"X-Emby-Token": api_key} if kind == "jellyfin" else {"X-Api-Key": api_key}
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(url.rstrip("/") + paths[kind], headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Verbinding mislukt: {exc}") from exc
        if response.status_code >= 400:
            raise HTTPException(502, f"{kind} antwoordde met HTTP {response.status_code}")
        return {"integratie": kind, "bereikbaar": True, "statuscode": response.status_code}

    @router.post("/integraties/jellyfin/test")
    async def jellyfin_test(body: IntegrationTest, request: Request) -> dict[str, Any]:
        return await integration_test("jellyfin", body, request)

    @router.post("/integraties/radarr/test")
    async def radarr_test(body: IntegrationTest, request: Request) -> dict[str, Any]:
        return await integration_test("radarr", body, request)

    @router.post("/integraties/sonarr/test")
    async def sonarr_test(body: IntegrationTest, request: Request) -> dict[str, Any]:
        return await integration_test("sonarr", body, request)

    @router.get("/integraties")
    async def integrations(request: Request) -> list[dict[str, Any]]:
        database = _database(request)
        setting = database.fetch_one(
            "SELECT jellyfin_url,radarr_url,sonarr_url FROM instelling WHERE id=1"
        )
        assert setting is not None
        store = SecretStore(database, _settings(request))
        return [
            {
                "naam": kind,
                "url": setting[f"{kind}_url"],
                "sleutel_ingesteld": store.configured(f"{kind}_api_sleutel"),
            }
            for kind in ("jellyfin", "radarr", "sonarr")
        ]

    @router.patch("/integraties/{kind}")
    async def configure_integration(
        kind: Literal["jellyfin", "radarr", "sonarr"],
        body: IntegrationConfig,
        request: Request,
    ) -> dict[str, Any]:
        if body.url is not None and body.url and not body.url.startswith(("http://", "https://")):
            raise HTTPException(422, "Integratie-URL moet met http:// of https:// beginnen")
        database = _database(request)
        if body.url is not None:
            database.execute(f"UPDATE instelling SET {kind}_url=? WHERE id=1", (body.url or None,))
        store = SecretStore(database, _settings(request))
        try:
            if body.verwijder_sleutel:
                store.delete(f"{kind}_api_sleutel")
            elif body.api_sleutel:
                store.set(f"{kind}_api_sleutel", body.api_sleutel)
        except SecretError as exc:
            raise HTTPException(409, str(exc)) from exc
        row = database.fetch_one(f"SELECT {kind}_url AS url FROM instelling WHERE id=1")
        return {
            "naam": kind,
            "url": row["url"] if row else None,
            "sleutel_ingesteld": store.configured(f"{kind}_api_sleutel"),
        }

    return router


__all__ = ["create_api_router", "_worker_rows"]
