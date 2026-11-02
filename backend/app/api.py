"""REST-routes voor mutaties en historische gegevens; live data gaat via /ws."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import psutil
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import __version__
from app.ai_controller import (
    AI_ACTIVE_CONFIRMATION,
    AI_SECRET_KEY,
    HDR10_CONFIRMATION,
    AiController,
    revoke_ai_consent,
)
from app.backups import BackupError, create_backup
from app.backups import verify_backup as verify_backup_copy
from app.bronnen import (
    SourceError,
    ensure_smb_mount,
    parse_smb_location,
    resolve_local_source,
    test_source,
)
from app.config import Settings
from app.db import Database
from app.geheimen import SecretError, SecretStore
from app.health import dependency_report
from app.kwaliteit import (
    dolby_vision_overview,
    quality_directory,
    run_profile_trial,
    trial_overview,
    verification_overview,
    vmaf_available,
)
from app.lan import (
    LAN_CONFIRMATION,
    LanConfigError,
    authorization_matches,
    hash_password,
    lan_urls,
    validate_username,
)
from app.media.beslissen import STANDAARD, Besluit, beslis
from app.metingen import system_metric_history
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
from app.scanscheduler import LibraryScanScheduler, record_scan_failure
from app.systeem import GpuMonitor, mount_report, qsv_test, system_metrics
from app.uitvoerder import ExecutionError, create_job_from_decision
from app.vervanging import temporary_replacement_path
from app.wachtrijherstel import (
    QueueRecoveryError,
    media_process_alive,
    queue_recovery_report,
    recover_queue,
    remove_job_quality_output,
    remove_job_temporary_output,
)

LIBRARY_DELETE_CONFIRMATION = "BIBLIOTHEEK VERWIJDEREN"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LibraryCreate(StrictModel):
    naam: str = Field(min_length=1, max_length=120)
    pad: str | None = None
    locatie: str | None = None
    smb_gebruikersnaam: str | None = Field(default=None, max_length=256)
    smb_wachtwoord: str | None = Field(default=None, max_length=1024)
    smb_gast: bool = False
    profiel_id: int
    scan_interval: Literal[0, 86400, 604800] = 86400
    scan_tijd: str = "03:00"

    @field_validator("scan_tijd")
    @classmethod
    def validate_scan_time(cls, value: str) -> str:
        try:
            parsed = time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Scantijd moet UU:MM zijn") from exc
        if parsed.second or parsed.microsecond:
            raise ValueError("Scantijd mag alleen uren en minuten bevatten")
        return parsed.strftime("%H:%M")


class LibraryPatch(StrictModel):
    naam: str | None = Field(default=None, min_length=1, max_length=120)
    profiel_id: int | None = None
    scan_interval: Literal[0, 86400, 604800] | None = None
    scan_tijd: str | None = None
    actief: bool | None = None

    @field_validator("scan_tijd")
    @classmethod
    def validate_scan_time(cls, value: str | None) -> str | None:
        return LibraryCreate.validate_scan_time(value) if value is not None else None


class LibraryDelete(StrictModel):
    bevestiging: str


class CanaryRelease(StrictModel):
    bevestiging: str


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
    aantal: int | None = Field(default=None, ge=0)
    nice: int | None = Field(default=None, ge=-20, le=19)
    stilte_start: str | None = None
    stilte_einde: str | None = None


class QueueRecoveryRequest(StrictModel):
    bevestiging: str


class SettingsPatch(StrictModel):
    server_naam: str | None = Field(default=None, min_length=1, max_length=120)
    tijdzone: str | None = Field(default=None, min_length=1, max_length=100)
    log_niveau: Literal["debug", "info", "warning", "error"] | None = None
    cpu_max_percent: int | None = Field(default=None, ge=10, le=100)
    cpu_max_temperatuur: int | None = Field(default=None, ge=40, le=110)
    max_gelijktijdige_encodes: int | None = Field(default=None, ge=1)
    cache_waarschuwing_gb: int | None = Field(default=None, ge=1)
    cache_pauze_gb: int | None = Field(default=None, ge=1)
    hardlink_modus: Literal["overslaan", "verbreek_na_bevestiging"] | None = None
    meld_browser_fouten: bool | None = None
    meld_browser_batches: bool | None = None
    pauzeer_cpu_bij_kijken: bool | None = None

    @field_validator("tijdzone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Onbekende IANA-tijdzone") from exc
        return value


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


class LanAccessPatch(StrictModel):
    ingeschakeld: bool
    gebruikersnaam: str | None = Field(default=None, min_length=3, max_length=64)
    wachtwoord: str | None = Field(default=None, min_length=12, max_length=128)
    bevestiging: str | None = None


class IntegrationTest(StrictModel):
    url: str | None = None
    api_sleutel: str | None = Field(default=None, min_length=1)


class IntegrationConfig(StrictModel):
    url: str | None = None
    api_sleutel: str | None = Field(default=None, min_length=1)
    verwijder_sleutel: bool = False


class AiConfigPatch(StrictModel):
    ingeschakeld: bool
    modus: Literal["schaduw", "actief"]
    maandbudget_usd: float = Field(default=10.0, ge=0.10, le=100.0)
    api_sleutel: str | None = Field(default=None, min_length=20, max_length=500)
    verwijder_sleutel: bool = False
    hdr10_vrijgegeven: bool = False
    geheugenherstel_ingeschakeld: bool = True
    bevestiging: str | None = None
    hdr10_bevestiging: str | None = None


def _database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _gpu(request: Request) -> GpuMonitor:
    return cast(GpuMonitor, request.app.state.gpu_monitor)


def _scheduler(request: Request) -> LibraryScanScheduler:
    return cast(LibraryScanScheduler, request.app.state.scan_scheduler)


def _ai_controller(request: Request) -> AiController:
    return cast(AiController, request.app.state.ai_controller)


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _canary_report(database: Database, library_id: int) -> dict[str, Any]:
    library = _get_row(database, "library", library_id)
    feature = database.fetch_one(
        "SELECT ingeschakeld,configuratie FROM functie WHERE sleutel='canary_limiet'"
    )
    configuration = (
        _json_object(str(feature["configuratie"])) if feature is not None else {}
    )
    limits = {
        "max_bestanden": int(configuration.get("max_bestanden", 50)),
        "max_gb": int(configuration.get("max_gb", 100)),
        "max_jobs_per_dag": int(configuration.get("max_jobs_per_dag", 10)),
    }
    summary = database.fetch_one(
        """
        SELECT COUNT(j.id) AS totaal,
               SUM(CASE WHEN j.status='klaar' THEN 1 ELSE 0 END) AS klaar,
               SUM(CASE WHEN j.status='wacht' THEN 1 ELSE 0 END) AS wacht,
               SUM(CASE WHEN j.status IN ('bezig','verifieren') THEN 1 ELSE 0 END) AS actief,
               SUM(CASE WHEN j.status='mislukt' THEN 1 ELSE 0 END) AS mislukt,
               SUM(CASE WHEN v.geslaagd=1 THEN 1 ELSE 0 END) AS verificaties_ok,
               SUM(CASE WHEN v.geslaagd=0 THEN 1 ELSE 0 END) AS verificaties_mislukt,
               COALESCE(SUM(CASE WHEN j.status='klaar'
                                 THEN COALESCE(j.bron_grootte,f.grootte) ELSE 0 END),0)
                   AS bron_bytes,
               COALESCE(SUM(CASE WHEN j.status='klaar'
                                 THEN COALESCE(j.resultaat_grootte,0) ELSE 0 END),0)
                   AS resultaat_bytes,
               SUM(CASE WHEN date(j.gestart_op)=date('now') THEN 1 ELSE 0 END) AS vandaag
        FROM job j JOIN bestand f ON f.id=j.bestand_id
        LEFT JOIN verificatie v ON v.job_id=j.id
        WHERE f.library_id=?
        """,
        (library_id,),
    )
    assert summary is not None
    completed = int(summary["klaar"] or 0)
    source_bytes = int(summary["bron_bytes"] or 0)
    today = int(summary["vandaag"] or 0)
    limit_reached = (
        completed >= limits["max_bestanden"]
        or source_bytes >= limits["max_gb"] * 1024**3
        or today >= limits["max_jobs_per_dag"]
    )
    return {
        "library_id": library_id,
        "status": str(library["canary_status"]),
        "ingeschakeld": bool(feature is not None and int(feature["ingeschakeld"])),
        "limieten": limits,
        "limiet_bereikt": limit_reached,
        "totaal_jobs": int(summary["totaal"] or 0),
        "klaar": completed,
        "wacht": int(summary["wacht"] or 0),
        "actief": int(summary["actief"] or 0),
        "mislukt": int(summary["mislukt"] or 0),
        "verificaties_ok": int(summary["verificaties_ok"] or 0),
        "verificaties_mislukt": int(summary["verificaties_mislukt"] or 0),
        "bron_bytes": source_bytes,
        "resultaat_bytes": int(summary["resultaat_bytes"] or 0),
        "bespaard_bytes": max(0, source_bytes - int(summary["resultaat_bytes"] or 0)),
        "vandaag": today,
        "kan_vrijgeven": (
            str(library["canary_status"]) == "actief"
            and completed > 0
            and int(summary["actief"] or 0) == 0
        ),
    }


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
    for key in (
        "doel_bitrate_2160_kbps",
        "doel_bitrate_1080_kbps",
        "doel_bitrate_720_kbps",
    ):
        try:
            bitrate = int(merged[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"{key} moet een geheel getal zijn") from exc
        if bitrate < 0 or bitrate > 200_000:
            raise HTTPException(422, f"{key} moet tussen 0 en 200000 kbps liggen")
        merged[key] = bitrate
    for key in ("svt_threads_2160", "svt_threads_1080", "svt_threads_720"):
        try:
            threads = int(merged[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"{key} moet een geheel getal zijn") from exc
        if threads < 0 or threads > 128:
            raise HTTPException(422, f"{key} moet 0 (automatisch) of maximaal 128 zijn")
        merged[key] = threads
    return merged


def _hdr_autonomy_readiness(database: Database) -> dict[str, Any]:
    rows = database.fetch_all(
        """
        SELECT b.encoder,
               SUM(CASE WHEN v.geslaagd=1 AND v.hdr_bron=1 AND v.hdr_resultaat=1
                         AND v.md_gelijk=1 THEN 1 ELSE 0 END) AS geslaagd,
               SUM(CASE WHEN v.hdr_bron=1 AND (v.geslaagd=0 OR v.hdr_resultaat!=1
                         OR v.md_gelijk!=1) THEN 1 ELSE 0 END) AS afwijkingen
        FROM verificatie v JOIN job j ON j.id=v.job_id
        JOIN besluit b ON b.id=j.besluit_id
        WHERE b.encoder IN ('libsvtav1','av1_qsv')
        GROUP BY b.encoder
        """
    )
    counts = {
        str(row["encoder"]): {
            "geslaagd": int(row["geslaagd"] or 0),
            "afwijkingen": int(row["afwijkingen"] or 0),
        }
        for row in rows
    }
    cpu = counts.get("libsvtav1", {"geslaagd": 0, "afwijkingen": 0})
    qsv = counts.get("av1_qsv", {"geslaagd": 0, "afwijkingen": 0})
    ready = (
        cpu["geslaagd"] > 0
        and qsv["geslaagd"] > 0
        and cpu["afwijkingen"] == 0
        and qsv["afwijkingen"] == 0
    )
    return {
        "gereed": ready,
        "cpu": cpu,
        "qsv": qsv,
        "tv_test_vereist": False,
        "uitleg": (
            "CPU en QSV vereisen ieder minstens één exact teruggelezen HDR10-test; "
            "iedere metadata-afwijking blokkeert autonomie. De bewuste keuze zonder "
            "tv-test wordt bij vrijgave geaudit."
        ),
    }


async def _prepare_library_source(
    settings: Settings, body: LibraryCreate
) -> tuple[Path, Literal["lokaal", "smb"], str, dict[str, Any]]:
    if bool(body.pad) == bool(body.locatie):
        raise HTTPException(422, "Geef precies pad (oud) of locatie op")
    raw = (body.locatie or body.pad or "").strip()
    try:
        if raw.lower().startswith("smb://"):
            location = parse_smb_location(raw)
            mount_path = await asyncio.to_thread(
                ensure_smb_mount,
                location,
                username=body.smb_gebruikersnaam,
                password=body.smb_wachtwoord,
                guest=body.smb_gast,
            )
            path = resolve_local_source(settings, str(mount_path))
            source_type: Literal["lokaal", "smb"] = "smb"
            source_uri = location.uri
        else:
            if body.smb_gebruikersnaam or body.smb_wachtwoord or body.smb_gast:
                raise SourceError("SMB-accountvelden horen alleen bij een smb://-locatie")
            path = resolve_local_source(settings, raw)
            source_type = "lokaal"
            source_uri = str(path)
        report = await asyncio.to_thread(test_source, path)
    except SourceError as exc:
        raise HTTPException(422, str(exc)) from exc
    return path, source_type, source_uri, report


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
    except ScanError as exc:
        # De scan_run bevat bij een gewone scan de hervatbare toestand; de UI
        # haalt de concrete fout via diagnose/logging op.
        if not dry_run:
            record_scan_failure(database, library_id, str(exc))


def _worker_rows(database: Database) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    memory = psutil.virtual_memory()
    limit_row = database.fetch_one(
        "SELECT max_gelijktijdige_encodes FROM instelling WHERE id=1"
    )
    total_limit = int(limit_row["max_gelijktijdige_encodes"]) if limit_row else 1
    for row in database.fetch_all("SELECT * FROM worker_config ORDER BY baan"):
        item = _as_bool_fields(dict(row))
        item["veilige_max_workers"] = total_limit
        item["totaal_max_actieve_jobs"] = total_limit
        item["geheugen_totaal_bytes"] = int(memory.total)
        item["geheugen_beschikbaar_bytes"] = int(memory.available)
        item["veiligheidsuitleg"] = (
            f"De totale encodegrens staat op {total_limit}. Deze host heeft "
            f"{memory.total / 1024**3:.1f} GiB RAM; SVT-4K kiest automatisch een "
            "veilige paralleliteit uit beschikbaar geheugen. Na een RAM-upgrade "
            "kan de totale grens worden verhoogd. Een mislukte job krijgt maximaal "
            "één automatische herstelpoging."
        )
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
            SELECT l.*,p.naam AS profiel_naam,
                   (SELECT COUNT(*) FROM bestand f
                    WHERE f.library_id=l.id AND f.aanwezig=1) AS aantal_bestanden,
                   (SELECT COALESCE(SUM(f.grootte),0) FROM bestand f
                    WHERE f.library_id=l.id AND f.aanwezig=1) AS totale_grootte,
                   (SELECT COALESCE(SUM(s.bytes_voor-s.bytes_na),0) FROM besparing s
                    WHERE s.library_id=l.id) AS bespaard_bytes,
                   (SELECT COALESCE(SUM(s.bytes_voor),0) FROM besparing s
                    WHERE s.library_id=l.id) AS verwerkte_bron_bytes,
                   (SELECT COUNT(*) FROM job j JOIN bestand f ON f.id=j.bestand_id
                    WHERE f.library_id=l.id AND j.status='klaar') AS verwerkt,
                   (SELECT COUNT(*) FROM bestand f WHERE f.library_id=l.id
                    AND f.scan_status='wacht_op_stabiliteit') AS stabiliteitswachtrij,
                   COALESCE((SELECT s.nieuw FROM scan_run s WHERE s.library_id=l.id
                    ORDER BY s.id DESC LIMIT 1),0) AS laatste_scan_nieuw,
                   COALESCE((SELECT s.gewijzigd FROM scan_run s WHERE s.library_id=l.id
                    ORDER BY s.id DESC LIMIT 1),0) AS laatste_scan_gewijzigd,
                   COALESCE((SELECT s.verdwenen FROM scan_run s WHERE s.library_id=l.id
                    ORDER BY s.id DESC LIMIT 1),0) AS laatste_scan_verdwenen,
                   (SELECT COUNT(*) FROM besluit b JOIN bestand f ON f.id=b.bestand_id
                    WHERE f.library_id=l.id AND b.goedkeuringsbron='ai_policy'
                    AND b.status IN ('goedgekeurd','ingepland')) AS ai_goedgekeurd,
                   (SELECT COUNT(*) FROM besluit b JOIN bestand f ON f.id=b.bestand_id
                    LEFT JOIN ai_analyse a ON a.id=b.ai_analyse_id
                    WHERE f.library_id=l.id AND b.status='nieuw'
                    AND (a.id IS NULL
                         OR (a.modus='schaduw'
                             AND a.policy_uitkomst NOT LIKE 'geblokkeerd:%')
                         OR a.policy_uitkomst LIKE 'mens nodig:%'))
                    AS handmatig_wachtend,
                   (SELECT COUNT(*) FROM besluit b JOIN bestand f ON f.id=b.bestand_id
                    JOIN ai_analyse a ON a.id=b.ai_analyse_id
                    WHERE f.library_id=l.id AND b.status='nieuw'
                    AND a.policy_uitkomst LIKE 'geblokkeerd:%') AS ai_geblokkeerd
            FROM library l JOIN profiel p ON p.id=l.profiel_id
            ORDER BY l.naam
            """
        )
        result = []
        for row in rows:
            item = _as_bool_fields(dict(row))
            item["bron_controle"] = (
                _json_object(str(item["bron_controle"])) if item.get("bron_controle") else None
            )
            result.append(item)
        return result

    @router.post("/libraries", status_code=status.HTTP_201_CREATED)
    async def add_library(body: LibraryCreate, request: Request) -> dict[str, Any]:
        database = _database(request)
        _get_row(database, "profiel", body.profiel_id)
        path, source_type, source_uri, source_report = await _prepare_library_source(
            _settings(request), body
        )
        try:
            row_id = database.execute_insert(
                """
                INSERT INTO library(
                    naam,pad,profiel_id,scan_interval,scan_tijd,verwacht_device,
                    bron_type,bron_uri,bron_controle,bron_gecontroleerd_op
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    body.naam,
                    str(path),
                    body.profiel_id,
                    body.scan_interval,
                    body.scan_tijd,
                    path.stat().st_dev,
                    source_type,
                    source_uri,
                    json.dumps(source_report, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Deze library bestaat al") from exc
        _scheduler(request).schedule_library(row_id)
        result = dict(_get_row(database, "library", row_id))
        result["bron_controle"] = source_report
        return result

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
        if {"scan_interval", "scan_tijd", "actief"} & set(values):
            _scheduler(request).schedule_library(library_id)
        if "profiel_id" in values:
            revoke_ai_consent(
                database,
                library_ids=[library_id],
                reason="AI-toestemming ingetrokken door gewijzigd libraryprofiel",
            )
        return _as_bool_fields(dict(_get_row(database, "library", library_id)))

    @router.delete("/libraries/{library_id}")
    async def delete_library(
        library_id: int, body: LibraryDelete, request: Request
    ) -> dict[str, Any]:
        """Verwijder pipelineadministratie zonder ooit het bronpad aan te raken."""

        if body.bevestiging != LIBRARY_DELETE_CONFIRMATION:
            raise HTTPException(
                409,
                f"Typ ter bevestiging exact: {LIBRARY_DELETE_CONFIRMATION}",
            )
        database = _database(request)
        task_prefix = f"library-{library_id}-"

        def active_background_tasks() -> list[str]:
            return [
                identifier
                for identifier, task in request.app.state.background_tasks.items()
                if identifier.startswith(task_prefix) and not task.done()
            ]

        if active_background_tasks():
            raise HTTPException(
                409,
                "Deze bibliotheek heeft nog een actieve scan of proefdraai; "
                "wacht tot die klaar is",
            )
        if _scheduler(request).current_library_id == library_id:
            raise HTTPException(409, "De geplande scan van deze bibliotheek is nog actief")

        planner_tasks = getattr(request.app.state.planner, "tasks", {})
        active_planner_ids = {
            int(job_id) for job_id, task in planner_tasks.items() if not task.done()
        }
        with database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            library = connection.execute(
                "SELECT * FROM library WHERE id=?", (library_id,)
            ).fetchone()
            if library is None:
                connection.rollback()
                raise HTTPException(404, "Bibliotheek niet gevonden")
            jobs = connection.execute(
                "SELECT j.id,j.status,j.proces_pid FROM job j "
                "JOIN bestand f ON f.id=j.bestand_id WHERE f.library_id=? ORDER BY j.id",
                (library_id,),
            ).fetchall()
            active_jobs = [
                int(row["id"])
                for row in jobs
                if str(row["status"]) in {"bezig", "verifieren"}
                or int(row["id"]) in active_planner_ids
                or media_process_alive(
                    int(row["proces_pid"]) if row["proces_pid"] is not None else None
                )
            ]
            if active_jobs:
                connection.rollback()
                raise HTTPException(
                    409,
                    "Stop of herstel eerst de actieve jobs van deze bibliotheek: "
                    + ", ".join(f"#{job_id}" for job_id in active_jobs),
                )
            active_scan = connection.execute(
                "SELECT id FROM scan_run WHERE library_id=? AND status='bezig' LIMIT 1",
                (library_id,),
            ).fetchone()
            if active_scan is not None:
                connection.rollback()
                raise HTTPException(409, "Deze bibliotheek wordt nog gescand")
            unsafe_replacement = connection.execute(
                """
                SELECT v.id FROM vervang_transactie v
                JOIN job j ON j.id=v.job_id
                JOIN bestand f ON f.id=j.bestand_id
                WHERE f.library_id=?
                  AND v.status NOT IN ('hersteld','opgeruimd')
                  AND NOT (v.status='vervangen' AND j.status='klaar')
                LIMIT 1
                """,
                (library_id,),
            ).fetchone()
            if unsafe_replacement is not None:
                connection.rollback()
                raise HTTPException(
                    409,
                    "Deze bibliotheek heeft een onafgeronde vervanging; "
                    "los die eerst op onder Systeem",
                )

            # Inactief zetten sluit planner, scheduler en AI-controller af voordat
            # lokale pipeline-output buiten de transactie wordt opgeruimd.
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE library SET actief=0,volgende_scan=NULL WHERE id=?",
                (library_id,),
            )
            connection.execute(
                """
                UPDATE job
                SET status='afgebroken',fout='bibliotheek uit pipeline verwijderd',
                    foutsoort='LibraryVerwijderd',klaar_op=?,proces_pid=NULL,
                    worker_id=NULL,cache_reservering=0
                WHERE status IN ('wacht','mislukt')
                  AND bestand_id IN (SELECT id FROM bestand WHERE library_id=?)
                """,
                (now, library_id),
            )
            connection.execute(
                """
                UPDATE besluit
                SET status='afgewezen',reden=reden || '; bibliotheek uit pipeline verwijderd'
                WHERE status IN ('nieuw','goedgekeurd','ingepland')
                  AND bestand_id IN (SELECT id FROM bestand WHERE library_id=?)
                """,
                (library_id,),
            )
            connection.commit()

        removed_paths = 0
        try:
            for row in jobs:
                job_id = int(row["id"])
                removed_paths += len(
                    remove_job_temporary_output(_settings(request), job_id)
                )
                removed_paths += len(remove_job_quality_output(_settings(request), job_id))
        except QueueRecoveryError as exc:
            raise HTTPException(
                409,
                "De bibliotheek is veilig uitgezet, maar lokale pipeline-output kon "
                f"niet volledig worden opgeruimd: {exc}. Probeer verwijderen opnieuw.",
            ) from exc

        if active_background_tasks() or _scheduler(request).current_library_id == library_id:
            raise HTTPException(
                409,
                "De bibliotheek is veilig uitgezet, maar er werd nog een achtergrondtaak "
                "gevonden; probeer verwijderen opnieuw zodra die klaar is",
            )

        with database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT naam,pad,bron_type,bron_uri,actief FROM library WHERE id=?",
                (library_id,),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise HTTPException(404, "Bibliotheek niet gevonden")
            if int(current["actief"]):
                connection.rollback()
                raise HTTPException(
                    409,
                    "De bibliotheek is intussen opnieuw geactiveerd; verwijderen is gestopt",
                )
            if connection.execute(
                "SELECT 1 FROM scan_run WHERE library_id=? AND status='bezig' LIMIT 1",
                (library_id,),
            ).fetchone():
                connection.rollback()
                raise HTTPException(409, "De bibliotheek is intussen opnieuw gaan scannen")
            if connection.execute(
                """
                SELECT 1 FROM job j JOIN bestand f ON f.id=j.bestand_id
                WHERE f.library_id=? AND j.status IN ('bezig','verifieren') LIMIT 1
                """,
                (library_id,),
            ).fetchone():
                connection.rollback()
                raise HTTPException(409, "De bibliotheek heeft intussen een actieve job")

            counts = {
                "bestanden": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM bestand WHERE library_id=?", (library_id,)
                    ).fetchone()[0]
                ),
                "besluiten": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM besluit WHERE bestand_id IN "
                        "(SELECT id FROM bestand WHERE library_id=?)",
                        (library_id,),
                    ).fetchone()[0]
                ),
                "jobs": len(jobs),
                "scans": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM scan_run WHERE library_id=?", (library_id,)
                    ).fetchone()[0]
                ),
                "ai_analyses": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM ai_analyse
                        WHERE library_id=?
                           OR job_id IN (
                               SELECT j.id FROM job j JOIN bestand f ON f.id=j.bestand_id
                               WHERE f.library_id=?
                           )
                           OR besluit_id IN (
                               SELECT b.id FROM besluit b JOIN bestand f ON f.id=b.bestand_id
                               WHERE f.library_id=?
                           )
                        """,
                        (library_id, library_id, library_id),
                    ).fetchone()[0]
                ),
            }

            connection.execute(
                "UPDATE besluit SET ai_analyse_id=NULL WHERE bestand_id IN "
                "(SELECT id FROM bestand WHERE library_id=?)",
                (library_id,),
            )
            connection.execute(
                "UPDATE job_poging SET ai_analyse_id=NULL WHERE job_id IN "
                "(SELECT j.id FROM job j JOIN bestand f ON f.id=j.bestand_id "
                "WHERE f.library_id=?)",
                (library_id,),
            )
            connection.execute(
                """
                DELETE FROM ai_analyse
                WHERE library_id=?
                   OR job_id IN (
                       SELECT j.id FROM job j JOIN bestand f ON f.id=j.bestand_id
                       WHERE f.library_id=?
                   )
                   OR besluit_id IN (
                       SELECT b.id FROM besluit b JOIN bestand f ON f.id=b.bestand_id
                       WHERE f.library_id=?
                   )
                """,
                (library_id, library_id, library_id),
            )
            for table in ("verificatie", "vervang_transactie", "logregel", "job_poging"):
                connection.execute(
                    f"DELETE FROM {table} WHERE job_id IN "
                    "(SELECT j.id FROM job j JOIN bestand f ON f.id=j.bestand_id "
                    "WHERE f.library_id=?)",
                    (library_id,),
                )
            connection.execute(
                """
                DELETE FROM kwaliteitsmeting
                WHERE bestand_id IN (SELECT id FROM bestand WHERE library_id=?)
                   OR job_id IN (
                       SELECT j.id FROM job j JOIN bestand f ON f.id=j.bestand_id
                       WHERE f.library_id=?
                   )
                """,
                (library_id, library_id),
            )
            connection.execute(
                "DELETE FROM besparing WHERE library_id=? OR job_id IN "
                "(SELECT j.id FROM job j JOIN bestand f ON f.id=j.bestand_id "
                "WHERE f.library_id=?)",
                (library_id, library_id),
            )
            connection.execute(
                "DELETE FROM job WHERE bestand_id IN "
                "(SELECT id FROM bestand WHERE library_id=?)",
                (library_id,),
            )
            connection.execute(
                "DELETE FROM besluit WHERE bestand_id IN "
                "(SELECT id FROM bestand WHERE library_id=?)",
                (library_id,),
            )
            connection.execute("DELETE FROM bestand WHERE library_id=?", (library_id,))
            connection.execute("DELETE FROM scan_run WHERE library_id=?", (library_id,))
            connection.execute("DELETE FROM library WHERE id=?", (library_id,))
            audit = {
                "naam": str(current["naam"]),
                "pad": str(current["pad"]),
                "bron_uri": str(current["bron_uri"] or ""),
                "bron_ongemoeid": True,
                "smb_mount_behouden": str(current["bron_type"]) == "smb",
                "verwijderd": counts,
                "lokale_pipeline_mappen": removed_paths,
            }
            connection.execute(
                "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) "
                "VALUES ('bibliotheek','verwijderd',?,NULL,?)",
                (json.dumps(audit, ensure_ascii=False), datetime.now(UTC).isoformat()),
            )
            connection.commit()

        return {
            "id": library_id,
            "naam": str(current["naam"]),
            "verwijderd": True,
            "bron_ongemoeid": True,
            "mediapad_verwijderd": False,
            "smb_mount_behouden": str(current["bron_type"]) == "smb",
            "pipeline_records": counts,
            "lokale_pipeline_mappen_verwijderd": removed_paths,
        }

    @router.get("/libraries/{library_id}/canary")
    async def canary_report(library_id: int, request: Request) -> dict[str, Any]:
        return _canary_report(_database(request), library_id)

    @router.post("/libraries/{library_id}/canary-vrijgeven")
    async def release_canary(
        library_id: int, body: CanaryRelease, request: Request
    ) -> dict[str, Any]:
        if body.bevestiging != "CANARY-BATCH VRIJGEVEN":
            raise HTTPException(409, "Typ ter bevestiging exact: CANARY-BATCH VRIJGEVEN")
        database = _database(request)
        report = _canary_report(database, library_id)
        if report["status"] == "vrijgegeven":
            return report
        if not report["kan_vrijgeven"]:
            raise HTTPException(
                409,
                "Vrijgave vereist minstens één voltooide job en geen actieve job "
                "in deze bibliotheek",
            )
        database.execute(
            "UPDATE library SET canary_status='vrijgegeven' WHERE id=? AND canary_status='actief'",
            (library_id,),
        )
        database.execute(
            "UPDATE job SET fout=NULL WHERE bestand_id IN "
            "(SELECT id FROM bestand WHERE library_id=?) "
            "AND status='wacht' AND fout LIKE '%canary-batch%'",
            (library_id,),
        )
        return _canary_report(database, library_id)

    async def start_scan(library_id: int, request: Request, dry_run: bool) -> dict[str, Any]:
        library = _get_row(_database(request), "library", library_id)
        if not int(library["actief"]):
            raise HTTPException(409, "Deze bibliotheek is niet actief")
        task = asyncio.create_task(_scan_background(_database(request), library_id, dry_run))
        return {
            "taak_id": _task_id(
                request,
                task,
                f"library-{library_id}-scan-{uuid.uuid4()}",
            ),
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
            {
                "id": row["id"],
                "naam": row["naam"],
                # Voeg nieuwe veilige profielvelden ook aan oude profielen toe,
                # zodat de bitrate- en geheugenkeuzes direct zichtbaar zijn.
                "instellingen": _profile_settings(_json_object(row["inst"])),
            }
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
        if body.instellingen is not None:
            affected = [
                int(item["id"])
                for item in database.fetch_all(
                    "SELECT id FROM library WHERE profiel_id=?", (profile_id,)
                )
            ]
            revoke_ai_consent(
                database,
                library_ids=affected,
                reason="AI-toestemming ingetrokken door gewijzigde profielinstellingen",
            )
        return {"id": profile_id, "naam": name, "instellingen": values}

    @router.post("/profielen/{profile_id}/proefdraai", status_code=status.HTTP_202_ACCEPTED)
    async def trial(profile_id: int, body: TrialRequest, request: Request) -> dict[str, Any]:
        database = _database(request)
        _get_row(database, "profiel", profile_id)
        file_row = database.fetch_one(
            "SELECT id,pad,library_id FROM bestand WHERE id=?", (body.bestand_id,)
        )
        if file_row is None:
            raise HTTPException(404, "Bestand bestaat niet in de catalogus")
        feature = database.fetch_one("SELECT ingeschakeld FROM functie WHERE sleutel='proefdraai'")
        if feature is None or not int(feature["ingeschakeld"]):
            raise HTTPException(409, "Schakel de optionele functie proefdraai eerst in")
        identifier = f"library-{int(file_row['library_id'])}-trial-{uuid.uuid4()}"
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
                "hardlink_bevestigd=?,goedkeuringsbron='mens' WHERE id=?",
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
        if int(probe.get("dv_profile") or 0) and body.encoder == "av1_qsv":
            raise HTTPException(
                409,
                "Dolby Vision blijft CPU-only: av1_qsv kan de RPU niet dragen; "
                "metadata terugmuxen is geen geldige DV-vervanging",
            )
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
            "hardlink_bevestigd=?,goedkeuringsbron='mens' "
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
        other = database.fetch_one(
            "SELECT id,status FROM job WHERE bestand_id=? AND id<>? "
            "AND status IN ('wacht','bezig','verifieren','klaar') "
            "ORDER BY CASE status WHEN 'klaar' THEN 0 ELSE 1 END,id LIMIT 1",
            (int(row["bestand_id"]), job_id),
        )
        if other is not None:
            raise HTTPException(
                409,
                f"Job {int(other['id'])} bewaakt dit bestand al met status "
                f"{str(other['status'])}; een dubbele encode is geblokkeerd",
            )
        planner_tasks = getattr(request.app.state.planner, "tasks", {})
        if job_id in planner_tasks and not planner_tasks[job_id].done():
            raise HTTPException(409, "Deze job heeft nog een actieve achtergrondtaak")
        if media_process_alive(
            int(row["proces_pid"]) if row["proces_pid"] is not None else None
        ):
            raise HTTPException(409, "Deze job heeft nog een actief mediaproces")
        try:
            remove_job_temporary_output(_settings(request), job_id)
        except QueueRecoveryError as exc:
            raise HTTPException(409, str(exc)) from exc
        database.execute(
            """
            UPDATE job SET status='wacht',fout=NULL,foutsoort=NULL,afbreken_gevraagd=0,
                           percent=0,proces_pid=NULL,worker_id=NULL,klaar_op=NULL,
                           cache_reservering=0 WHERE id=?
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
                   max_gelijktijdige_encodes,cache_waarschuwing_gb,cache_pauze_gb,hardlink_modus,
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
        if "tijdzone" in values:
            _scheduler(request).initialize_schedule()
        return await get_settings(request)

    @router.get("/ai-controller")
    async def get_ai_controller(request: Request) -> dict[str, Any]:
        database = _database(request)
        row = database.fetch_one(
            "SELECT ingeschakeld,configuratie,laatste_fout,gewijzigd_op "
            "FROM functie WHERE sleutel='ai_controller'"
        )
        if row is None:
            raise HTTPException(500, "AI-controllerconfiguratie ontbreekt")
        configuration = _json_object(str(row["configuratie"]))
        dv_feature = database.fetch_one(
            "SELECT ingeschakeld,configuratie FROM functie WHERE sleutel='dv_experimenteel'"
        )
        dv_configuration = (
            _json_object(str(dv_feature["configuratie"])) if dv_feature is not None else {}
        )
        dv_released = bool(
            dv_feature
            and dv_feature["ingeschakeld"]
            and dv_configuration.get("tv_test_geslaagd", False)
        )
        month = datetime.now(UTC).strftime("%Y-%m")
        usage = database.fetch_one(
            """
            SELECT COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(kosten_microdollar),0) AS kosten_microdollar,
                   COUNT(*) AS analyses
            FROM ai_analyse WHERE substr(gemaakt_op,1,7)=?
            """,
            (month,),
        )
        return {
            "ingeschakeld": bool(row["ingeschakeld"]),
            "modus": str(configuration.get("modus", "schaduw")),
            "provider": "openai",
            "model": str(configuration.get("model", "gpt-6-luna")),
            "reasoning": str(configuration.get("reasoning", "medium")),
            "maandbudget_usd": float(configuration.get("maandbudget_usd", 10)),
            "hdr10_vrijgegeven": bool(configuration.get("hdr10_vrijgegeven", False)),
            "dv_vrijgegeven": dv_released,
            "geheugenherstel_ingeschakeld": bool(
                configuration.get("geheugenherstel_ingeschakeld", True)
            ),
            "api_sleutel_ingesteld": SecretStore(
                database, _settings(request)
            ).configured(AI_SECRET_KEY),
            "laatste_fout": row["laatste_fout"],
            "gewijzigd_op": row["gewijzigd_op"],
            "verbruik": {
                "maand": month,
                "input_tokens": int(usage["input_tokens"] if usage else 0),
                "output_tokens": int(usage["output_tokens"] if usage else 0),
                "kosten_usd": round(
                    int(usage["kosten_microdollar"] if usage else 0) / 1_000_000, 6
                ),
                "analyses": int(usage["analyses"] if usage else 0),
            },
            "hdr10_poort": _hdr_autonomy_readiness(database),
            "bevoegdheden": [
                "deterministisch veilig overslaan",
                "waarschuwingvrije SDR-hercodering en -opschoning",
                *(
                    ["TV-geteste Dolby Vision-profielen 7 en 8 via de CPU"]
                    if dv_released
                    else []
                ),
                "maximaal één automatische herstelpoging per mislukte job",
                "joblokale geheugenveilige CPU- of GPU-instelling bij een encoderfout",
                "joblokale lagere doelbitrate als verificatie een groter resultaat meet",
            ],
            "altijd_handmatig": [
                (
                    "opruimen, hardlinks, HDR10+, Dolby Vision profiel 5 en canary-vrijgave"
                    if dv_released
                    else "opruimen, hardlinks, HDR10+, Dolby Vision en canary-vrijgave"
                ),
                "encoder- en baanwissels en iedere fout na de eerste herstelpoging",
                "alle overige bitrate-, CRF-, HDR- of Dolby-Visionwijzigingen",
                "verificatie-, metadata-, ACL/xattr-, mount- en herstelconflicten",
            ],
        }

    @router.patch("/ai-controller")
    async def update_ai_controller(
        body: AiConfigPatch, request: Request
    ) -> dict[str, Any]:
        database = _database(request)
        row = database.fetch_one(
            "SELECT ingeschakeld,configuratie FROM functie WHERE sleutel='ai_controller'"
        )
        if row is None:
            raise HTTPException(500, "AI-controllerconfiguratie ontbreekt")
        if body.ingeschakeld and body.modus == "actief":
            if body.bevestiging != AI_ACTIVE_CONFIRMATION:
                raise HTTPException(409, f"Typ ter bevestiging exact: {AI_ACTIVE_CONFIRMATION}")
            if not body.api_sleutel and not SecretStore(
                database, _settings(request)
            ).configured(AI_SECRET_KEY):
                raise HTTPException(422, "Stel eerst de OpenAI API-sleutel in")
        readiness = _hdr_autonomy_readiness(database)
        if body.hdr10_vrijgegeven:
            if body.hdr10_bevestiging != HDR10_CONFIRMATION:
                raise HTTPException(409, f"Typ ter bevestiging exact: {HDR10_CONFIRMATION}")
            if not readiness["gereed"]:
                raise HTTPException(
                    409,
                    "HDR10 blijft geblokkeerd tot CPU en QSV exact groene metadata-encodes hebben",
                )
        if body.verwijder_sleutel and body.api_sleutel:
            raise HTTPException(422, "Kies sleutel instellen of verwijderen, niet beide")
        if body.verwijder_sleutel and body.ingeschakeld:
            raise HTTPException(422, "Schakel AI eerst uit voordat je de API-sleutel verwijdert")
        store = SecretStore(database, _settings(request))
        try:
            if body.api_sleutel:
                store.set(AI_SECRET_KEY, body.api_sleutel)
            elif body.verwijder_sleutel:
                store.delete(AI_SECRET_KEY)
        except SecretError as exc:
            raise HTTPException(422, str(exc)) from exc
        old_configuration = _json_object(str(row["configuratie"]))
        configuration = {
            **old_configuration,
            "modus": body.modus,
            "provider": "openai",
            "model": "gpt-6-luna",
            "reasoning": "medium",
            "maandbudget_usd": body.maandbudget_usd,
            "hdr10_vrijgegeven": body.hdr10_vrijgegeven,
            "geheugenherstel_ingeschakeld": body.geheugenherstel_ingeschakeld,
            "prompt_versie": "2",
        }
        now = datetime.now(UTC).isoformat()
        database.execute(
            """
            UPDATE functie
            SET ingeschakeld=?,configuratie=?,laatste_fout=NULL,gewijzigd_op=?
            WHERE sleutel='ai_controller'
            """,
            (
                int(body.ingeschakeld),
                json.dumps(configuration, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        database.execute(
            "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) "
            "VALUES ('ai','controller',?,?,?)",
            (
                json.dumps(
                    {
                        "ingeschakeld": bool(row["ingeschakeld"]),
                        "modus": old_configuration.get("modus"),
                        "hdr10_vrijgegeven": old_configuration.get(
                            "hdr10_vrijgegeven", False
                        ),
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ingeschakeld": body.ingeschakeld,
                        "modus": body.modus,
                        "hdr10_vrijgegeven": body.hdr10_vrijgegeven,
                        "geheugenherstel_ingeschakeld": body.geheugenherstel_ingeschakeld,
                        "tv_test_bewust_overgeslagen": body.hdr10_vrijgegeven,
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
        return await get_ai_controller(request)

    @router.post("/ai-controller/analyseer", status_code=status.HTTP_202_ACCEPTED)
    async def analyze_ai_now(request: Request) -> dict[str, int]:
        return await _ai_controller(request).tick()

    @router.get("/ai-controller/analyses")
    async def ai_analyses(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        rows = _database(request).fetch_all(
            """
            SELECT a.id,a.soort,a.library_id,a.besluit_id,a.job_id,a.provider,a.model,
                   a.modus,a.classificatie,a.voorgestelde_actie,a.policy_uitkomst,
                   a.uitgevoerd,a.voorstel,a.input_tokens,a.output_tokens,
                   a.kosten_microdollar,a.fout,a.gemaakt_op,l.naam AS library_naam
            FROM ai_analyse a
            LEFT JOIN library l ON l.id=a.library_id
            ORDER BY a.id DESC LIMIT ?
            """,
            (limit,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            proposal = _json_object(str(item.get("voorstel") or "{}"))
            item["redencodes"] = proposal.get("redencodes", [])
            item["bewijsvelden"] = proposal.get("bewijsvelden", [])
            item["vertrouwen"] = proposal.get("vertrouwen")
            item["retry_aanpassing"] = proposal.get("retry_aanpassing", "geen")
            item["kosten_usd"] = round(int(item["kosten_microdollar"]) / 1_000_000, 6)
            # De ruwe JSON bevat geen extra informatie meer en hoeft niet naar de browser.
            item.pop("voorstel", None)
            result.append(item)
        return result

    @router.get("/ai-controller/statistieken")
    async def ai_statistics(
        request: Request,
        dagen: int = Query(default=30, ge=7, le=365),
    ) -> dict[str, Any]:
        database = _database(request)
        setting = database.fetch_one("SELECT tijdzone FROM instelling WHERE id=1")
        timezone_name = str(
            setting["tijdzone"] if setting is not None else "Europe/Amsterdam"
        )
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
            timezone_name = "UTC"

        now = datetime.now(UTC)
        local_today = now.astimezone(zone).date()
        first_day = local_today - timedelta(days=dagen - 1)
        first_moment = datetime.combine(first_day, time.min, tzinfo=zone).astimezone(UTC)
        rows = database.fetch_all(
            """
            SELECT classificatie,uitgevoerd,input_tokens,output_tokens,
                   kosten_microdollar,fout,gemaakt_op
            FROM ai_analyse
            WHERE gemaakt_op>=?
            ORDER BY gemaakt_op
            """,
            (first_moment.isoformat(),),
        )
        per_day: dict[str, dict[str, Any]] = {}
        for offset in range(dagen):
            day = first_day + timedelta(days=offset)
            per_day[day.isoformat()] = {
                "dag": day.isoformat(),
                "analyses": 0,
                "beslissingen": 0,
                "overeenkomstig": 0,
                "tijdelijk": 0,
                "mens_nodig": 0,
                "geblokkeerd": 0,
                "provider_fouten": 0,
                "uitgevoerd": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "kosten_microdollar": 0,
                "kosten_usd": 0.0,
            }
        classifications = {"overeenkomstig", "tijdelijk", "mens_nodig", "geblokkeerd"}
        for row in rows:
            try:
                made_at = datetime.fromisoformat(str(row["gemaakt_op"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if made_at.tzinfo is None:
                made_at = made_at.replace(tzinfo=UTC)
            bucket = per_day.get(made_at.astimezone(zone).date().isoformat())
            if bucket is None:
                continue
            bucket["analyses"] += 1
            classification = str(row["classificatie"] or "")
            if classification in classifications:
                bucket["beslissingen"] += 1
                bucket[classification] += 1
            if row["fout"]:
                bucket["provider_fouten"] += 1
            bucket["uitgevoerd"] += int(row["uitgevoerd"] or 0)
            bucket["input_tokens"] += int(row["input_tokens"] or 0)
            bucket["output_tokens"] += int(row["output_tokens"] or 0)
            bucket["kosten_microdollar"] += int(row["kosten_microdollar"] or 0)

        totals = {
            key: sum(int(day[key]) for day in per_day.values())
            for key in (
                "analyses",
                "beslissingen",
                "overeenkomstig",
                "tijdelijk",
                "mens_nodig",
                "geblokkeerd",
                "provider_fouten",
                "uitgevoerd",
                "input_tokens",
                "output_tokens",
                "kosten_microdollar",
            )
        }
        for day_bucket in per_day.values():
            day_bucket["kosten_usd"] = round(
                day_bucket["kosten_microdollar"] / 1_000_000, 6
            )
        return {
            "dagen": dagen,
            "tijdzone": timezone_name,
            "totaal": {
                **totals,
                "kosten_usd": round(totals["kosten_microdollar"] / 1_000_000, 6),
            },
            "per_dag": list(per_day.values()),
        }

    @router.get("/lan-toegang")
    async def get_lan_access(request: Request) -> dict[str, Any]:
        row = _database(request).fetch_one(
            "SELECT ingeschakeld,configuratie,laatste_fout,gewijzigd_op "
            "FROM functie WHERE sleutel='externe_toegang'"
        )
        if row is None:
            raise HTTPException(500, "LAN-toegangsconfiguratie ontbreekt")
        configuration = _json_object(str(row["configuratie"]))
        username = configuration.get("gebruikersnaam")
        return {
            "ingeschakeld": bool(row["ingeschakeld"]),
            "gebruikersnaam": username if isinstance(username, str) else "transcode",
            "wachtwoord_ingesteld": isinstance(configuration.get("wachtwoord_hash"), str),
            "adressen": lan_urls(),
            "laatste_fout": row["laatste_fout"],
            "gewijzigd_op": row["gewijzigd_op"],
            "uitleg": (
                "Alleen apparaten op een privénetwerk worden toegelaten. "
                "FastAPI en poort 8000 blijven uitsluitend lokaal bereikbaar."
            ),
        }

    @router.get("/lan-toegang/autoriseer", include_in_schema=False)
    async def authorize_lan_access(request: Request) -> Response:
        row = _database(request).fetch_one(
            "SELECT ingeschakeld,configuratie FROM functie WHERE sleutel='externe_toegang'"
        )
        if row is None or not bool(row["ingeschakeld"]):
            raise HTTPException(403, "LAN-toegang staat uit")
        configuration = _json_object(str(row["configuratie"]))
        if not authorization_matches(configuration, request.headers.get("authorization")):
            raise HTTPException(
                401,
                "Ongeldige LAN-aanmelding",
                headers={"WWW-Authenticate": 'Basic realm="AV1 Pipeline", charset="UTF-8"'},
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.patch("/lan-toegang")
    async def update_lan_access(body: LanAccessPatch, request: Request) -> dict[str, Any]:
        database = _database(request)
        row = database.fetch_one(
            "SELECT ingeschakeld,configuratie FROM functie WHERE sleutel='externe_toegang'"
        )
        if row is None:
            raise HTTPException(500, "LAN-toegangsconfiguratie ontbreekt")
        old_configuration = _json_object(str(row["configuratie"]))
        username_value = body.gebruikersnaam or old_configuration.get("gebruikersnaam")
        try:
            username = validate_username(
                username_value if isinstance(username_value, str) else "transcode"
            )
            password_hash = old_configuration.get("wachtwoord_hash")
            if body.wachtwoord is not None:
                password_hash = hash_password(body.wachtwoord)
        except LanConfigError as exc:
            raise HTTPException(422, str(exc)) from exc
        if body.ingeschakeld:
            if body.bevestiging != LAN_CONFIRMATION:
                raise HTTPException(409, f"Typ ter bevestiging exact: {LAN_CONFIRMATION}")
            if not isinstance(password_hash, str):
                raise HTTPException(422, "Stel eerst een LAN-wachtwoord in")
        new_configuration: dict[str, Any] = {"gebruikersnaam": username}
        if isinstance(password_hash, str):
            new_configuration["wachtwoord_hash"] = password_hash
        changed = datetime.now(UTC).isoformat()
        database.execute(
            "UPDATE functie SET ingeschakeld=?,configuratie=?,laatste_fout=NULL,gewijzigd_op=? "
            "WHERE sleutel='externe_toegang'",
            (int(body.ingeschakeld), json.dumps(new_configuration), changed),
        )
        database.execute(
            "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) "
            "VALUES ('functie','externe_toegang',?,?,?)",
            (
                json.dumps(
                    {
                        "ingeschakeld": bool(row["ingeschakeld"]),
                        "gebruikersnaam": old_configuration.get("gebruikersnaam"),
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"ingeschakeld": body.ingeschakeld, "gebruikersnaam": username},
                    ensure_ascii=False,
                ),
                changed,
            ),
        )
        return await get_lan_access(request)

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
            "ai_controller": (
                "Begrensde AI-controle in schaduw- of actieve modus; lokale policy beslist."
            ),
        }
        result = []
        for row in _database(request).fetch_all("SELECT * FROM functie ORDER BY sleutel"):
            item = _as_bool_fields(dict(row))
            configuration = _json_object(str(item["configuratie"]))
            if item["sleutel"] == "externe_toegang":
                configuration.pop("wachtwoord_hash", None)
                configuration["wachtwoord_ingesteld"] = bool(
                    _json_object(str(row["configuratie"])).get("wachtwoord_hash")
                )
            item["configuratie"] = configuration
            item["uitleg"] = descriptions.get(str(item["sleutel"]), "Optionele functie")
            result.append(item)
        return result

    @router.patch("/functies/{key}")
    async def update_feature(key: str, body: FeaturePatch, request: Request) -> dict[str, Any]:
        database = _database(request)
        row = database.fetch_one("SELECT * FROM functie WHERE sleutel=?", (key,))
        if row is None:
            raise HTTPException(404, "Onbekende functie")
        if key == "externe_toegang":
            raise HTTPException(409, "Gebruik de afgeschermde LAN-toegangsinstelling")
        if key == "ai_controller":
            raise HTTPException(409, "Gebruik de afgeschermde AI-controllerinstelling")
        confirmations = {
            "hardlink_verbreken": (True, "HARDLINKS VERBREKEN"),
            "dv_experimenteel": (True, "EXPERIMENTEEL DOLBY VISION"),
            "canary_limiet": (False, "CANARY-LIMIET UITZETTEN"),
        }
        risky = confirmations.get(key)
        if (
            risky is not None
            and body.ingeschakeld == risky[0]
            and bool(row["ingeschakeld"]) != body.ingeschakeld
            and body.bevestiging != risky[1]
        ):
            raise HTTPException(409, f"Typ ter bevestiging exact: {risky[1]}")
        old_configuration = _json_object(str(row["configuratie"]))
        if (
            key == "dv_experimenteel"
            and body.configuratie.get("tv_test_geslaagd") is True
            and old_configuration.get("tv_test_geslaagd") is not True
            and body.bevestiging != "DOLBY VISION TV-TEST GESLAAGD"
        ):
            raise HTTPException(
                409, "Typ ter bevestiging exact: DOLBY VISION TV-TEST GESLAAGD"
            )
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

    @router.get("/systeem/wachtrijherstel")
    async def queue_recovery_status(request: Request) -> dict[str, Any]:
        tasks = getattr(request.app.state.planner, "tasks", {})
        active_task_ids = {
            int(job_id) for job_id, task in tasks.items() if not task.done()
        }
        return queue_recovery_report(
            _database(request), _settings(request), active_task_ids
        )

    @router.post("/systeem/wachtrijherstel")
    async def run_queue_recovery(
        body: QueueRecoveryRequest, request: Request
    ) -> dict[str, Any]:
        if body.bevestiging != "WACHTRIJ VEILIG HERSTELLEN":
            raise HTTPException(409, "Typ ter bevestiging exact: WACHTRIJ VEILIG HERSTELLEN")
        database = _database(request)
        tasks = getattr(request.app.state.planner, "tasks", {})
        active_task_ids = {
            int(job_id) for job_id, task in tasks.items() if not task.done()
        }
        previous = database.fetch_all(
            "SELECT baan,gepauzeerd,pauze_reden FROM worker_config ORDER BY baan"
        )
        database.execute(
            "UPDATE worker_config SET gepauzeerd=1,pauze_reden='tijdelijk: wachtrijherstel'"
        )
        try:
            result = recover_queue(
                database, _settings(request), active_task_ids
            )
        except QueueRecoveryError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            for row in previous:
                database.execute(
                    "UPDATE worker_config SET gepauzeerd=?,pauze_reden=? WHERE baan=?",
                    (int(row["gepauzeerd"]), row["pauze_reden"], str(row["baan"])),
                )
        database.execute(
            "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) "
            "VALUES ('wachtrijherstel','tijdelijke-output',NULL,?,?)",
            (json.dumps(result, ensure_ascii=False), datetime.now(UTC).isoformat()),
        )
        return result

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

    @router.get("/systeem/metingen")
    async def system_measurements(
        request: Request,
        uren: int = Query(default=1, ge=1, le=24 * 7),
    ) -> list[dict[str, Any]]:
        """Historische meetpunten voor het dashboard; live ticks blijven via WS."""

        return system_metric_history(_database(request), uren)

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

    @router.get("/kwaliteit/dolby-vision")
    async def dolby_vision_status(request: Request) -> dict[str, Any]:
        return dolby_vision_overview(_database(request))

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
        expected = temporary_replacement_path(source, int(row["job_id"]))
        # Bestaande transacties van vóór de korte-naamreparatie blijven via de
        # UI veilig opruimbaar; nieuwe transacties gebruiken uitsluitend de
        # gehashte korte naam.
        legacy = source.with_name(f".{source.name}.av1-pipeline-{int(row['job_id'])}.deel")
        if temporary not in {expected, legacy} or temporary.parent != source.parent:
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
