"""FastAPI-applicatie, herstelvolgorde en levenscyclus van de native backend."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app import __version__
from app.api import create_api_router
from app.config import Settings, settings
from app.db import Database
from app.health import dependency_report
from app.integraties import IntegrationMonitor
from app.live import register_websocket
from app.media.beslissen import STANDAARD
from app.onderhoud import maintenance_loop
from app.planner import Planner
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
        if validate_storage:
            app_settings.validate_storage()
        database.initialize()
        _ensure_default_profile(database)
        recovery = recover_replacements(database, app_settings)
        if recovery:
            LOGGER.warning("%d vervangtransacties beoordeeld bij opstart", len(recovery))
        gpu_enabled = database.fetch_one(
            "SELECT ingeschakeld FROM functie WHERE sleutel='gpu_monitor'"
        )
        if gpu_enabled is not None and int(gpu_enabled["ingeschakeld"]):
            await application.state.gpu_monitor.start()
        planner_task: asyncio.Task[None] | None = None
        integration_task = asyncio.create_task(application.state.integration_monitor.run_forever())
        maintenance_task = asyncio.create_task(maintenance_loop(database, app_settings))
        if start_planner:
            planner_task = asyncio.create_task(application.state.planner.run_forever())
        yield
        application.state.planner.stopping = True
        application.state.integration_monitor.stopping = True
        if planner_task is not None:
            await planner_task
        integration_task.cancel()
        with suppress(asyncio.CancelledError):
            await integration_task
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
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
    application.state.integration_monitor = IntegrationMonitor(database, app_settings)
    application.state.background_tasks = {}
    application.include_router(create_api_router())
    register_websocket(application)

    @application.get("/health", include_in_schema=False)
    async def root_health() -> dict[str, object]:
        dependencies = await dependency_report()
        database_ok = database.reachable()
        return {
            "status": ("ok" if database_ok and dependencies["media_stack_gereed"] else "degraded"),
            "versie": __version__,
            "database": "ok" if database_ok else "fout",
            "dependencies": dependencies,
        }

    return application


app = create_app()
