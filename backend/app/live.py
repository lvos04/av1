"""Eén WebSocket met live ticks, toestandsovergangen en gerichte logtail."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.api import _worker_rows
from app.config import Settings
from app.db import Database
from app.systeem import GpuMonitor, system_metrics


def _queue_counts(database: Database) -> dict[str, int]:
    row = database.fetch_one(
        """
        SELECT SUM(CASE WHEN status='wacht' THEN 1 ELSE 0 END) AS wachtend,
               SUM(CASE WHEN status IN ('bezig','verifieren') THEN 1 ELSE 0 END) AS bezig,
               SUM(CASE WHEN status='klaar' AND substr(klaar_op,1,10)=? THEN 1 ELSE 0 END)
                   AS klaar_vandaag,
               SUM(CASE WHEN status='mislukt' THEN 1 ELSE 0 END) AS fouten
        FROM job
        """,
        (date.today().isoformat(),),
    )
    if row is None:
        return {"wachtend": 0, "bezig": 0, "klaar_vandaag": 0, "fouten": 0}
    names = ("wachtend", "bezig", "klaar_vandaag", "fouten")
    return {name: int(row[name] or 0) for name in names}


def _current_size(settings: Settings, job_id: int) -> int:
    directory = settings.cache_root / str(job_id)
    if not directory.is_dir():
        return 0
    try:
        return sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
    except OSError:
        return 0


def tick(database: Database, settings: Settings, gpu: GpuMonitor) -> dict[str, Any]:
    workers = _worker_rows(database)
    active: list[dict[str, Any]] = []
    for lane in workers:
        jobs = lane.pop("actieve_jobs")
        lane.pop("historie")
        if not jobs:
            active.append(
                {
                    "baan": lane["baan"],
                    "job_id": None,
                    "gepauzeerd": lane["gepauzeerd"],
                    "pauze_reden": lane["pauze_reden"],
                }
            )
        for job in jobs:
            job["baan"] = lane["baan"]
            job["grootte_nu"] = _current_size(settings, int(job["job_id"]))
            active.append(job)
    return {
        "type": "tick",
        "tijd": date.today().isoformat(),
        "workers": active,
        "systeem": system_metrics(database, settings, gpu),
        "wachtrij": _queue_counts(database),
    }


def _job_states(database: Database) -> dict[int, tuple[str, int]]:
    rows = database.fetch_all(
        "SELECT id,status,COALESCE(bron_grootte,0)-COALESCE(resultaat_grootte,0) AS besparing "
        "FROM job"
    )
    return {int(row["id"]): (str(row["status"]), int(row["besparing"] or 0)) for row in rows}


def _scan_states(database: Database) -> dict[int, tuple[str, int, int]]:
    rows = database.fetch_all("SELECT id,status,library_id,gevonden FROM scan_run")
    return {
        int(row["id"]): (str(row["status"]), int(row["library_id"]), int(row["gevonden"]))
        for row in rows
    }


async def _send_events(
    websocket: WebSocket,
    database: Database,
    previous_jobs: dict[int, tuple[str, int]],
    previous_scans: dict[int, tuple[str, int, int]],
) -> tuple[dict[int, tuple[str, int]], dict[int, tuple[str, int, int]]]:
    jobs = _job_states(database)
    for job_id, (job_status, saving) in jobs.items():
        old_status = previous_jobs.get(job_id, (job_status, 0))[0]
        if old_status != job_status and job_status == "klaar":
            await websocket.send_json(
                {"type": "job_klaar", "job_id": job_id, "besparing_bytes": saving}
            )
        elif old_status != job_status and job_status == "mislukt":
            row = database.fetch_one("SELECT fout FROM job WHERE id=?", (job_id,))
            await websocket.send_json(
                {
                    "type": "job_mislukt",
                    "job_id": job_id,
                    "reden": str(row["fout"] if row is not None else "onbekend"),
                }
            )
    scans = _scan_states(database)
    for scan_id, (scan_status, library_id, found) in scans.items():
        old_status = previous_scans.get(scan_id, (scan_status, library_id, found))[0]
        if old_status != scan_status and scan_status == "klaar":
            await websocket.send_json(
                {
                    "type": "scan_klaar",
                    "library_id": library_id,
                    "nieuw": found,
                    "gewijzigd": 0,
                }
            )
    return jobs, scans


async def _send_logs(
    websocket: WebSocket,
    settings: Settings,
    subscriptions: set[int],
    offsets: dict[int, int],
) -> None:
    for job_id in subscriptions:
        path = settings.config_root / "logs" / f"job-{job_id}.log"
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = offsets.get(job_id, len(lines))
        for line in lines[start : start + 250]:
            await websocket.send_json(
                {
                    "type": "log",
                    "job_id": job_id,
                    "niveau": "info",
                    "tekst": line,
                    "tijd": datetime_now(),
                }
            )
        offsets[job_id] = min(len(lines), start + 250)


async def _send_audits(websocket: WebSocket, database: Database, last_id: int) -> int:
    rows = database.fetch_all(
        "SELECT id,onderwerp,nieuw FROM auditlog WHERE soort='functie' AND id>? ORDER BY id",
        (last_id,),
    )
    for row in rows:
        await websocket.send_json(
            {
                "type": "melding",
                "soort": "functie",
                "titel": f"Onderdeel {row['onderwerp']} gewijzigd",
                "actie": str(row["nieuw"]),
            }
        )
        last_id = int(row["id"])
    return last_id


def datetime_now() -> str:
    return datetime.now(UTC).isoformat()


def register_websocket(application: FastAPI) -> None:
    @application.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        database: Database = application.state.database
        settings: Settings = application.state.settings
        gpu: GpuMonitor = application.state.gpu_monitor
        subscriptions: set[int] = set()
        offsets: dict[int, int] = {}
        job_states = _job_states(database)
        scan_states = _scan_states(database)
        audit = database.fetch_one("SELECT COALESCE(MAX(id),0) AS id FROM auditlog")
        last_audit_id = int(audit["id"]) if audit is not None else 0
        try:
            while True:
                await websocket.send_json(tick(database, settings, gpu))
                job_states, scan_states = await _send_events(
                    websocket, database, job_states, scan_states
                )
                await _send_logs(websocket, settings, subscriptions, offsets)
                last_audit_id = await _send_audits(websocket, database, last_audit_id)
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=1.0)
                except TimeoutError:
                    continue
                action = message.get("actie") if isinstance(message, dict) else None
                raw_job_id = message.get("job_id") if isinstance(message, dict) else None
                if action == "volg" and isinstance(raw_job_id, int):
                    subscriptions.add(raw_job_id)
                    path = settings.config_root / "logs" / f"job-{raw_job_id}.log"
                    offsets[raw_job_id] = (
                        len(path.read_text(encoding="utf-8", errors="replace").splitlines())
                        if path.is_file()
                        else 0
                    )
                elif action == "stop_volgen" and isinstance(raw_job_id, int):
                    subscriptions.discard(raw_job_id)
                    offsets.pop(raw_job_id, None)
        except WebSocketDisconnect:
            return


__all__ = ["register_websocket", "tick"]
