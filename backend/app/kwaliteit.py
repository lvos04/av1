"""Optionele kwaliteitsmetingen die lokale veiligheid nooit blokkeren."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database


async def vmaf_available() -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-filters",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except (FileNotFoundError, TimeoutError):
        return False
    text = output.decode("utf-8", errors="replace")
    return process.returncode == 0 and re.search(r"\blibvmaf\b", text) is not None


async def extract_frame(source: Path, target: Path, second: float) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(max(0.0, second)),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(target),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    del output
    return process.returncode == 0 and target.is_file() and target.stat().st_size > 0


async def compute_vmaf(
    result: Path, source: Path, start_second: float, duration: float
) -> float | None:
    """Meet alleen als het echte libvmaf-filter beschikbaar is; verzin nooit een score."""

    if not await vmaf_available():
        return None
    sample_duration = min(30.0, max(0.5, duration))
    command = [
        "ffmpeg",
        "-hide_banner",
        "-ss",
        str(max(0.0, start_second)),
        "-t",
        str(sample_duration),
        "-i",
        str(result),
        "-ss",
        str(max(0.0, start_second)),
        "-t",
        str(sample_duration),
        "-i",
        str(source),
        "-lavfi",
        "[0:v]scale=1920:1080:flags=bicubic[a];"
        "[1:v]scale=1920:1080:flags=bicubic[b];"
        "[a][b]libvmaf=n_threads=4",
        "-f",
        "null",
        "-",
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    if process.returncode != 0:
        return None
    match = re.search(rb"VMAF score:\s*([0-9]+(?:\.[0-9]+)?)", output)
    if match is None:
        return None
    score = float(match.group(1))
    return score if 0.0 <= score <= 100.0 else None


def quality_directory(settings: Settings, job_id: int) -> Path:
    return settings.config_root / "kwaliteit" / str(job_id)


async def capture_comparison_frames(
    database: Database,
    settings: Settings,
    job_id: int,
    source: Path,
    result: Path | None,
    duration: float,
) -> dict[str, str | float | None]:
    feature = database.fetch_one("SELECT ingeschakeld FROM functie WHERE sleutel='vmaf'")
    if feature is None or not int(feature["ingeschakeld"]):
        return {"bron": None, "resultaat": None, "vmaf": None}
    completed = database.fetch_one("SELECT COUNT(*) AS aantal FROM kwaliteitsmeting")
    if completed is not None and int(completed["aantal"]) >= 50:
        return {"bron": None, "resultaat": None, "vmaf": None}
    directory = quality_directory(settings, job_id)
    second = max(0.0, duration / 2)
    source_frame = directory / "bron.jpg"
    result_frame = directory / "resultaat.jpg"
    if not source_frame.exists():
        await extract_frame(source, source_frame, second)
    if result is not None:
        await extract_frame(result, result_frame, second)
    score = (
        await compute_vmaf(result, source, max(0.0, second - 15.0), min(30.0, duration))
        if result is not None
        else None
    )
    return {
        "bron": str(source_frame) if source_frame.is_file() else None,
        "resultaat": str(result_frame) if result_frame.is_file() else None,
        "vmaf": score,
    }


async def run_profile_trial(
    database: Database,
    settings: Settings,
    task_id: str,
    profile_id: int,
    file_id: int,
    crf_values: list[int],
) -> None:
    """Encodeer korte tijdelijke fragmenten; de catalogusbron blijft alleen-lezen."""

    row = database.fetch_one("SELECT pad,probe FROM bestand WHERE id=?", (file_id,))
    if row is None:
        raise ValueError("Bestand ontbreekt uit de catalogus")
    source = Path(str(row["pad"]))
    probe_data = json.loads(str(row["probe"] or "{}"))
    duration = float(probe_data.get("duration") or 0.0)
    if not source.is_file() or duration <= 0:
        raise ValueError("Bron of speelduur is niet beschikbaar voor de proefdraai")
    sample_duration = min(120.0, duration)
    start_second = max(0.0, (duration - sample_duration) / 2)
    directory = settings.work_root / "proefdraaien" / task_id
    directory.mkdir(parents=True, exist_ok=False)
    has_vmaf = await vmaf_available()
    try:
        for crf in crf_values:
            partial = directory / f"crf-{crf}.deel.mkv"
            result = directory / f"crf-{crf}.mkv"
            started = time.monotonic()
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(start_second),
                "-t",
                str(sample_duration),
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-c:v",
                "libsvtav1",
                "-preset",
                "10",
                "-crf",
                str(crf),
                str(partial),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await process.communicate()
            if process.returncode != 0:
                explanation = output.decode("utf-8", errors="replace")[-1000:]
                database.execute(
                    """
                    INSERT INTO kwaliteitsmeting(
                        profiel_id,bestand_id,taak_id,soort,status,crf,toelichting,gemaakt_op
                    ) VALUES (?,?,?,'proefdraai','mislukt',?,?,?)
                    """,
                    (profile_id, file_id, task_id, crf, explanation, datetime.now(UTC).isoformat()),
                )
                continue
            os.replace(partial, result)
            elapsed = max(1, round(time.monotonic() - started))
            score = (
                await compute_vmaf(result, source, start_second, sample_duration)
                if has_vmaf
                else None
            )
            database.execute(
                """
                INSERT INTO kwaliteitsmeting(
                    profiel_id,bestand_id,taak_id,soort,status,start_seconde,
                    duur_seconden,crf,vmaf_score,grootte,duur_encode_seconden,
                    toelichting,gemaakt_op
                ) VALUES (?,?,?,'proefdraai','klaar',?,?,?,?,?,?,?,?)
                """,
                (
                    profile_id,
                    file_id,
                    task_id,
                    start_second,
                    round(sample_duration),
                    crf,
                    score,
                    result.stat().st_size,
                    elapsed,
                    "VMAF gemeten" if score is not None else "libvmaf niet beschikbaar",
                    datetime.now(UTC).isoformat(),
                ),
            )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def trial_overview(database: Database, profile_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in database.fetch_all(
            """
            SELECT k.taak_id,k.status,k.crf,k.vmaf_score,k.grootte,
                   k.duur_encode_seconden,k.toelichting,k.gemaakt_op,f.pad
            FROM kwaliteitsmeting k JOIN bestand f ON f.id=k.bestand_id
            WHERE k.profiel_id=? AND k.soort='proefdraai'
            ORDER BY k.id DESC LIMIT 50
            """,
            (profile_id,),
        )
    ]


def verification_overview(database: Database) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in database.fetch_all(
            """
            SELECT j.id AS job_id,j.status,f.pad,v.hdr_bron,v.hdr_resultaat,
                   v.dv_bron,v.dv_resultaat,v.md_gelijk,v.audio_bron,v.audio_resultaat,
                   v.attachments_bron,v.attachments_resultaat,v.geslaagd,v.toelichting,
                   k.vmaf_score,k.bron_frame,k.resultaat_frame
            FROM job j JOIN bestand f ON f.id=j.bestand_id
            JOIN verificatie v ON v.job_id=j.id
            LEFT JOIN kwaliteitsmeting k ON k.job_id=j.id
            ORDER BY j.id DESC LIMIT 100
            """
        )
    ]
