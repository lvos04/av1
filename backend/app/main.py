"""FastAPI-applicatie, herstelvolgorde en levenscyclus van de native backend."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app import __version__
from app.ai_controller import AiController, reconcile_safety_fingerprint
from app.api import create_api_router
from app.config import ConfigError, Settings, settings
from app.db import Database
from app.health import dependency_report
from app.integraties import IntegrationMonitor
from app.live import register_websocket
from app.media.beslissen import STANDAARD
from app.metingen import system_metrics_loop
from app.onderhoud import maintenance_loop
from app.opslag import storage_validation_error
from app.planner import Planner
from app.scanscheduler import LibraryScanScheduler
from app.systeem import GpuMonitor
from app.vervanging import recover_replacements

LOGGER = logging.getLogger(__name__)


def _ensure_default_profile(database: Database) -> None:
    row = database.fetch_one("SELECT id FROM profiel LIMIT 1")
    if row is None:
        database.execute_insert(
            "INSERT INTO profiel(naam,inst) VALUES (?,?)",
            ("Standaard veilig", json.dumps(STANDAARD, ensure_ascii=False)),
        )


def create_app(
    app_settings: Settings = settings,
    app_database: Database | None = None,
    *,
    start_planner: bool = True,
    validate_storage: bool = True,
) -> FastAPI:
    database = app_database or Database(app_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        storage_error: str | None = None
        if validate_storage:
            try:
                app_settings.validate_storage()
            except ConfigError as exc:
                # De kleine controledatabase blijft op de systeemschijf bereikbaar,
                # zodat een ontbrekende latere werkschijf via de UI kan worden hersteld.
                storage_error = str(exc)
                LOGGER.error("Opslag niet gereed; workers blijven gepauzeerd: %s", exc)
        database.initialize()
        application.state.storage_error = storage_error
        if storage_error is not None:
            database.execute(
                "UPDATE worker_config SET gepauzeerd=1,pauze_reden=?",
                (f"opslag niet gereed: {storage_error}",),
            )
        _ensure_default_profile(database)
        reconcile_safety_fingerprint(database)
        application.state.scan_scheduler.initialize_schedule()
        recovery = recover_replacements(database, app_settings)
        if recovery:
            LOGGER.warning("%d vervangtransacties beoordeeld bij opstart", len(recovery))
        gpu_enabled = database.fetch_one(
            "SELECT ingeschakeld FROM functie WHERE sleutel='gpu_monitor'"
        )
        if gpu_enabled is not None and int(gpu_enabled["ingeschakeld"]):
            await application.state.gpu_monitor.start()
        planner_task: asyncio.Task[None] | None = None
        scan_scheduler_task: asyncio.Task[None] | None = None
        ai_controller_task: asyncio.Task[None] | None = None
        system_metrics_task = asyncio.create_task(
            system_metrics_loop(database, app_settings, application.state.gpu_monitor)
        )
        integration_task = asyncio.create_task(application.state.integration_monitor.run_forever())
        maintenance_task = asyncio.create_task(maintenance_loop(database, app_settings))
        if start_planner:
            planner_task = asyncio.create_task(application.state.planner.run_forever())
            scan_scheduler_task = asyncio.create_task(
                application.state.scan_scheduler.run_forever()
            )
            ai_controller_task = asyncio.create_task(
                application.state.ai_controller.run_forever()
            )
        yield
        application.state.planner.stopping = True
        application.state.scan_scheduler.stopping = True
        application.state.ai_controller.stopping = True
        application.state.integration_monitor.stopping = True
        if planner_task is not None:
            await planner_task
        if scan_scheduler_task is not None:
            await scan_scheduler_task
        if ai_controller_task is not None:
            await ai_controller_task
        integration_task.cancel()
        with suppress(asyncio.CancelledError):
            await integration_task
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
        system_metrics_task.cancel()
        with suppress(asyncio.CancelledError):
            await system_metrics_task
        await application.state.gpu_monitor.stop()
        tasks: dict[str, asyncio.Task[object]] = application.state.background_tasks
        for task in tasks.values():
            task.cancel()
        for task in list(tasks.values()):
            with suppress(asyncio.CancelledError):
                await task

    application = FastAPI(
        title="AV1 Pipeline",
        description=("Veilige, expliciet goedgekeurde AV1-pipeline voor één native Ubuntu-host."),
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.database = database
    application.state.gpu_monitor = GpuMonitor()
    application.state.planner = Planner(database, app_settings)
    application.state.scan_scheduler = LibraryScanScheduler(database)
    application.state.ai_controller = AiController(database, app_settings)
    application.state.integration_monitor = IntegrationMonitor(database, app_settings)
    application.state.background_tasks = {}
    application.state.storage_error = None
    application.include_router(create_api_router())
    register_websocket(application)

    @application.get("/health", include_in_schema=False)
    async def root_health() -> dict[str, object]:
        dependencies = await dependency_report()
        database_ok = database.reachable()
        application.state.storage_error = storage_validation_error(app_settings)
        return {
            "status": (
                "ok"
                if database_ok
                and dependencies["media_stack_gereed"]
                and application.state.storage_error is None
                else "degraded"
            ),
            "versie": __version__,
            "database": "ok" if database_ok else "fout",
            "opslag": (
                "ok" if application.state.storage_error is None else application.state.storage_error
            ),
            "dependencies": dependencies,
        }

    return application


app = create_app()
