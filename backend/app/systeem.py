"""Niet-kritieke hostmetingen en onschadelijke QSV-diagnostiek."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil

from app.config import Settings
from app.db import Database
from app.opslag import storage_validation_error


def _temperature() -> float | None:
    try:
        temperatures = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return None
    values = [entry.current for entries in temperatures.values() for entry in entries]
    return round(max(values), 1) if values else None


def mount_report(settings: Settings) -> dict[str, Any]:
    """Toon mount-identiteit expliciet; alleen een bestaande map is niet genoeg."""

    def details(path: Path) -> dict[str, Any]:
        exists = path.exists()
        try:
            stat = path.stat() if exists else None
            usage = shutil.disk_usage(path) if exists else None
        except OSError:
            stat = None
            usage = None
        return {
            "pad": str(path),
            "bestaat": exists,
            "mountpoint": path.is_mount() if exists else False,
            "device": stat.st_dev if stat is not None else None,
            "vrij_bytes": usage.free if usage is not None else None,
            "totaal_bytes": usage.total if usage is not None else None,
            "schrijfbaar": exists and os.access(path, os.W_OK | os.X_OK),
        }

    pipeline = details(settings.pipeline_root)
    workspace = details(settings.workspace_root)
    pipeline["schrijfbaar"] = all(
        path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        for path in (settings.config_root, settings.backup_root)
        if path.is_relative_to(settings.pipeline_root)
    )
    workspace["schrijfbaar"] = all(
        path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        for path in (settings.cache_root, settings.work_root)
    )
    workspace["modus"] = settings.storage_mode
    workspace["systeemschijf_reserve_gb"] = settings.system_disk_reserve_gb
    return {
        "pipeline_opslag": pipeline,
        "werkruimte": workspace,
        "library_root": details(settings.library_root),
        "cache": details(settings.cache_root),
    }


class GpuMonitor:
    """Optionele intel_gpu_top-lezer; een meetfout raakt de encoder nooit."""

    def __init__(self) -> None:
        self.latest: dict[str, Any] = {
            "video_percent": None,
            "render_percent": None,
            "freq_mhz": None,
            "power_w": None,
            "beschikbaar": False,
        }
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.task is not None and not self.task.done():
            return
        if shutil.which("intel_gpu_top") is None:
            self.latest["fout"] = "intel_gpu_top is niet geïnstalleerd"
            return
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            self.process = await asyncio.create_subprocess_exec(
                "intel_gpu_top",
                "-J",
                "-s",
                "1000",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if self.process.stdout is None:
                return
            buffer = ""
            while True:
                chunk = await self.process.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                objects, buffer = _json_objects(buffer)
                for value in objects:
                    self.latest = _gpu_values(value)
            stderr = await self.process.stderr.read() if self.process.stderr else b""
            if self.process.returncode is None:
                await self.process.wait()
            if not self.latest["beschikbaar"]:
                message = stderr.decode("utf-8", errors="replace").strip()
                self.latest["fout"] = message[-500:] or "GPU-monitor gaf geen meting"
        except (FileNotFoundError, OSError, ValueError) as exc:
            self.latest = {
                "video_percent": None,
                "render_percent": None,
                "freq_mhz": None,
                "power_w": None,
                "beschikbaar": False,
                "fout": str(exc),
            }
        finally:
            self.process = None

    async def stop(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            with suppress(TimeoutError):
                await asyncio.wait_for(self.process.wait(), timeout=3)
            if self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.task = None
        self.process = None

    async def restart(self, enabled: bool) -> dict[str, Any]:
        await self.stop()
        self.latest = {
            "video_percent": None,
            "render_percent": None,
            "freq_mhz": None,
            "power_w": None,
            "beschikbaar": False,
        }
        if enabled:
            await self.start()
        return dict(self.latest)


def _json_objects(buffer: str) -> tuple[list[dict[str, Any]], str]:
    """Haal complete JSON-objecten uit zowel array- als losse-objectuitvoer."""

    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(buffer):
        start = buffer.find("{", cursor)
        if start < 0:
            return objects, buffer[-1:]
        try:
            value, end = decoder.raw_decode(buffer[start:])
        except json.JSONDecodeError:
            return objects, buffer[start:]
        if isinstance(value, dict):
            objects.append(value)
        cursor = start + end
    return objects, ""


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("busy", "value", "actual", "GPU"):
            if key in value:
                found = _number(value[key])
                if found is not None:
                    return found
    return None


def _gpu_values(payload: dict[str, Any]) -> dict[str, Any]:
    raw_engines = payload.get("engines")
    engines: dict[str, Any] = raw_engines if isinstance(raw_engines, dict) else {}
    video_values = [
        _number(value) for name, value in engines.items() if str(name).lower().startswith("video")
    ]
    render_values = [
        _number(value) for name, value in engines.items() if "render" in str(name).lower()
    ]
    frequency = payload.get("frequency", {})
    power = payload.get("power", {})
    video = max((value for value in video_values if value is not None), default=None)
    render = max((value for value in render_values if value is not None), default=None)
    return {
        "video_percent": video,
        "render_percent": render,
        "freq_mhz": _number(frequency),
        "power_w": _number(power),
        "beschikbaar": video is not None or render is not None,
    }


def system_metrics(
    database: Database, settings: Settings, gpu_monitor: GpuMonitor
) -> dict[str, Any]:
    storage_error = storage_validation_error(settings)
    try:
        usage = shutil.disk_usage(settings.cache_root)
        free = usage.free
    except OSError as exc:
        free = 0
        storage_error = str(exc)
    reserved_row = database.fetch_one(
        "SELECT COALESCE(SUM(cache_reservering),0) AS bytes FROM job "
        "WHERE status IN ('bezig','verifieren')"
    )
    reserved = int(reserved_row["bytes"]) if reserved_row is not None else 0
    limits = database.fetch_one(
        "SELECT cache_waarschuwing_gb,cache_pauze_gb FROM instelling WHERE id=1"
    )
    warning = int(limits["cache_waarschuwing_gb"]) if limits is not None else 250
    pause = int(limits["cache_pauze_gb"]) if limits is not None else 150
    effective_warning, effective_pause = settings.effective_cache_limits(warning, pause)
    free_gb = free / 1024**3
    cache_status = (
        "pauzegrens"
        if free_gb < effective_pause
        else "waarschuwing"
        if free_gb < effective_warning
        else "voldoende"
    )
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_temp": _temperature(),
        "gpu": dict(gpu_monitor.latest),
        "schijf_vrij_gb": round(free / 1024**3, 2),
        "schijf_vrij_bytes": free,
        "schijf_gereserveerd_bytes": reserved,
        "schijf_beschikbaar_na_reservering": max(0, free - reserved),
        "cache_status": cache_status,
        "cache_waarschuwing_gb": warning,
        "cache_pauze_gb": pause,
        "effectieve_cache_waarschuwing_gb": effective_warning,
        "effectieve_cache_pauze_gb": effective_pause,
        "opslagmodus": settings.storage_mode,
        "systeemschijf_reserve_gb": settings.system_disk_reserve_gb,
        "opslagfout": storage_error,
    }


async def qsv_test(settings: Settings) -> dict[str, Any]:
    """Encodeer één seconde synthetisch beeld en verwijder elk tijdelijk bestand."""

    settings.validate_storage()
    output = ""
    with tempfile.TemporaryDirectory(prefix="qsv-test-", dir=settings.work_root) as directory:
        target = Path(directory) / "controle.ivf"
        arguments = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-init_hw_device",
            "qsv=hw,child_device=/dev/dri/renderD128",
            "-filter_hw_device",
            "hw",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=10:duration=1",
            "-vf",
            "format=nv12,hwupload=extra_hw_frames=16",
            "-an",
            "-c:v",
            "av1_qsv",
            "-global_quality",
            "35",
            "-f",
            "ivf",
            str(target),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
            output = stdout.decode("utf-8", errors="replace")
            succeeded = process.returncode == 0 and target.is_file() and target.stat().st_size > 0
        except (FileNotFoundError, TimeoutError) as exc:
            succeeded = False
            output = str(exc)
    return {
        "geslaagd": succeeded,
        "render_node": "/dev/dri/renderD128",
        "melding": "QSV-testencode geslaagd" if succeeded else output[-1000:],
        "tijdelijke_bestanden_opgeruimd": True,
    }
