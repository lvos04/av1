"""Server-side historie voor systeemgrafieken en een begrensde sampler."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.db import Database
from app.systeem import GpuMonitor, system_metrics

LOGGER = logging.getLogger(__name__)


def record_system_metric(
    database: Database, metrics: dict[str, Any], measured_at: datetime | None = None
) -> None:
    """Bewaar alleen numerieke meetwaarden; foutteksten horen niet in historie."""

    memory = metrics.get("geheugen") or {}
    gpu = metrics.get("gpu") or {}
    database.execute(
        """
        INSERT INTO systeem_meting(
            gemeten_op,cpu_percent,geheugen_percent,geheugen_gebruikt_bytes,
            gpu_video_percent,gpu_render_percent,gpu_power_w,package_power_w,
            platform_power_w
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            (measured_at or datetime.now(UTC)).isoformat(),
            float(metrics.get("cpu_percent") or 0),
            float(memory.get("percent") or 0),
            int(memory.get("gebruikt_bytes") or 0),
            gpu.get("video_percent"),
            gpu.get("render_percent"),
            gpu.get("power_w"),
            gpu.get("package_power_w"),
            gpu.get("platform_power_w"),
        ),
    )
    # Eén week historie is ruim genoeg voor het dashboard en voorkomt dat een
    # permanent draaiende host zijn SQLite-database ongemerkt laat groeien.
    cutoff = (measured_at or datetime.now(UTC)) - timedelta(days=7)
    database.execute("DELETE FROM systeem_meting WHERE gemeten_op<?", (cutoff.isoformat(),))


def system_metric_history(database: Database, hours: int = 1) -> list[dict[str, Any]]:
    """Lees serverhistorie in hetzelfde vlakke formaat als live WebSocket-metrics."""

    now = datetime.now(UTC)
    since = now - timedelta(hours=max(1, min(hours, 24 * 7)))
    rows = database.fetch_all(
        """
        SELECT gemeten_op,cpu_percent,geheugen_percent,geheugen_gebruikt_bytes,
               gpu_video_percent,gpu_render_percent,gpu_power_w,package_power_w,
               platform_power_w
        FROM systeem_meting WHERE gemeten_op>=? ORDER BY gemeten_op
        """,
        (since.isoformat(),),
    )
    return [
        {
            "tijd": str(row["gemeten_op"]),
            "cpu": float(row["cpu_percent"]),
            "gpuVideo": row["gpu_video_percent"],
            "gpuRender": row["gpu_render_percent"],
            "gpuPower": row["gpu_power_w"],
            "packagePower": row["package_power_w"],
            "platformPower": row["platform_power_w"],
            "memoryPercent": float(row["geheugen_percent"]),
            "memoryUsedBytes": int(row["geheugen_gebruikt_bytes"]),
        }
        for row in rows
    ]


async def system_metrics_loop(
    database: Database, settings: Settings, gpu: GpuMonitor, interval: float = 5.0
) -> None:
    """Meet buiten de browser om, zodat opnieuw inloggen de grafiek niet wist."""

    while True:
        try:
            values = await asyncio.to_thread(system_metrics, database, settings, gpu)
            await asyncio.to_thread(record_system_metric, database, values)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Een tijdelijke turbostat-/SQLite-fout mag de backend niet stoppen.
            LOGGER.warning("Systeemmeting kon niet worden opgeslagen: %s", exc)
        await asyncio.sleep(interval)


__all__ = ["record_system_metric", "system_metric_history", "system_metrics_loop"]
