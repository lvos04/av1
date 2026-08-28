"""Voer één expliciet goedgekeurde job veilig van cache tot vervanging uit."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.bestandsmetadata import MetadataError, capture_metadata
from app.config import Settings
from app.db import Database
from app.integraties import refresh_after_replacement
from app.kwaliteit import capture_comparison_frames
from app.media import commandos
from app.media.beslissen import STANDAARD, Besluit
from app.media.probe import probe
from app.onderhoud import append_job_log, record_failure
from app.verificatie import VerificationResult, verify_result
from app.vervanging import ReplacementError, atomic_replace


class ExecutionError(RuntimeError):
    """Een job is veilig gestopt; de bron is niet vervangen."""


VerificationHook = Callable[[VerificationResult], VerificationResult]


def _progress_number(value: str, suffix: str = "") -> float | None:
    text = value.removesuffix(suffix).strip()
    if text in {"", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def create_job_from_decision(database: Database, decision_id: int) -> int:
    row = database.fetch_one(
        """
        SELECT b.*,f.id AS bestand_id,f.nlink
        FROM besluit b JOIN bestand f ON f.id=b.bestand_id WHERE b.id=?
        """,
        (decision_id,),
    )
    if row is None:
        raise ExecutionError(f"Besluit {decision_id} bestaat niet")
    if row["status"] != "goedgekeurd":
        raise ExecutionError("Besluit moet eerst expliciet zijn goedgekeurd")
    if row["actie"] not in {"hercoderen", "opschonen"}:
        raise ExecutionError(f"Actie {row['actie']} is geen encodejob")
    if int(row["nlink"]) > 1 and not int(row["hardlink_bevestigd"]):
        raise ExecutionError("Hardlink is niet per bestand bevestigd")
    existing = database.fetch_one("SELECT id FROM job WHERE besluit_id=?", (decision_id,))
    if existing is not None:
        return int(existing["id"])
    job_id = database.execute_insert(
        """
        INSERT INTO job(besluit_id,bestand_id,baan,status,prioriteit)
        VALUES (?, ?, ?, 'wacht', 0)
        """,
        (decision_id, int(row["bestand_id"]), str(row["baan"] or "cpu")),
    )
    database.execute("UPDATE besluit SET status='ingepland' WHERE id=?", (decision_id,))
    return job_id


async def _run_process(
    database: Database,
    job_id: int,
    arguments: list[str],
    log_path: Path,
    duration: float,
) -> bool:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    database.execute(
        "UPDATE job SET proces_pid=?,commando=? WHERE id=?",
        (process.pid, json.dumps(arguments), job_id),
    )
    started = asyncio.get_running_loop().time()
    progress: dict[str, str] = {}
    with log_path.open("a", encoding="utf-8") as log_handle:
        while process.returncode is None:
            try:
                line_bytes = (
                    await asyncio.wait_for(process.stdout.readline(), timeout=0.25)
                    if process.stdout
                    else b""
                )
            except TimeoutError:
                line_bytes = b""
            if line_bytes:
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                log_handle.write(line + "\n")
                if line and not line.startswith(("frame=", "out_time_", "fps=", "speed=")):
                    append_job_log(database, job_id, "info", line)
                key, separator, value = line.partition("=")
                if separator and key in {"out_time_ms", "fps", "speed", "bitrate", "total_size"}:
                    progress[key] = value
            abort = database.fetch_one("SELECT afbreken_gevraagd FROM job WHERE id=?", (job_id,))
            if abort is not None and int(abort[0]):
                os.killpg(process.pid, signal.SIGTERM)
                with suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=10)
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
                return False
            now = asyncio.get_running_loop().time()
            if now - started >= 1.0 and progress:
                out_microseconds = _progress_number(progress.get("out_time_ms", ""))
                speed = _progress_number(progress.get("speed", ""), "x")
                bitrate = _progress_number(progress.get("bitrate", ""), "kbits/s")
                fps = _progress_number(progress.get("fps", ""))
                if out_microseconds is not None:
                    out_seconds = out_microseconds / 1_000_000
                    percent = min(100.0, out_seconds / duration * 100) if duration > 0 else 0.0
                    eta = (
                        int(max(0.0, duration - out_seconds) / speed)
                        if speed is not None and speed > 0
                        else None
                    )
                    database.execute(
                        """
                        UPDATE job SET percent=?,fps=?,speed=?,bitrate_kbps=?,eta_seconden=?
                        WHERE id=?
                        """,
                        (
                            percent,
                            fps,
                            speed,
                            bitrate,
                            eta,
                            job_id,
                        ),
                    )
                started = now
            if process.returncode is None:
                await asyncio.sleep(0)
        return process.returncode == 0


async def run_job(
    database: Database,
    settings: Settings,
    job_id: int,
    *,
    worker_id: str = "cli",
    verification_hook: VerificationHook | None = None,
    storage_validator: Callable[[], None] | None = None,
    command_override: list[str] | None = None,
    already_claimed: bool = False,
) -> bool:
    """Draai een job; iedere uitzondering wordt veilig als mislukking vastgelegd."""

    if storage_validator is None:
        storage_validator = settings.validate_storage
    storage_validator()
    if not already_claimed:
        claimed = database.execute(
            """
            UPDATE job SET status='bezig',worker_id=?,gestart_op=?,pogingen=pogingen+1
            WHERE id=? AND status='wacht'
            """,
            (worker_id, datetime.now(UTC).isoformat(), job_id),
        )
        if claimed != 1:
            raise ExecutionError("Job kon niet atomisch worden geclaimd")

    row = database.fetch_one(
        """
        SELECT j.*,b.payload,b.actie,b.hardlink_bevestigd,
               p.id AS profiel_id,p.inst AS profiel_inst,
               f.pad,f.grootte,f.mtime,
               f.nlink,f.library_id,l.pad AS library_pad,l.verwacht_device
        FROM job j
        JOIN besluit b ON b.id=j.besluit_id
        JOIN bestand f ON f.id=j.bestand_id
        JOIN library l ON l.id=f.library_id
        JOIN profiel p ON p.id=l.profiel_id
        WHERE j.id=?
        """,
        (job_id,),
    )
    if row is None:
        raise ExecutionError(f"Job {job_id} bestaat niet")
    source = Path(str(row["pad"]))
    cache_dir = settings.cache_root / str(job_id)
    work_dir = settings.work_root / str(job_id)
    log_dir = settings.config_root / "logs"
    cache_dir.mkdir(parents=True, exist_ok=False)
    work_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    partial = cache_dir / f"{source.name}.deel"
    result_path = cache_dir / f"{source.stem}.mkv"
    try:
        source_stat = source.stat()
        if source_stat.st_size != int(row["grootte"]) or source_stat.st_mtime != float(
            row["mtime"]
        ):
            raise ExecutionError("bron is sinds de scan gewijzigd")
        if source_stat.st_nlink > 1:
            feature = database.fetch_one(
                "SELECT ingeschakeld FROM functie WHERE sleutel='hardlink_verbreken'"
            )
            policy = database.fetch_one("SELECT hardlink_modus FROM instelling WHERE id=1")
            if (
                not int(row["hardlink_bevestigd"])
                or feature is None
                or not int(feature["ingeschakeld"])
                or policy is None
                or policy["hardlink_modus"] != "verbreek_na_bevestiging"
            ):
                raise ExecutionError("hardlink is vóór uitvoering veilig gestopt")
        if row["verwacht_device"] is not None and source_stat.st_dev != int(row["verwacht_device"]):
            raise ExecutionError("library staat niet meer op het verwachte device")
        metadata = capture_metadata(source)
        source_probe = probe(str(source))
        if source_probe is None:
            raise ExecutionError("bron is niet meer leesbaar")
        if bool(source_probe.get("hdr10plus")):
            database.execute(
                "UPDATE job SET status='overgeslagen',fout=?,klaar_op=? WHERE id=?",
                ("HDR10+ wordt altijd overgeslagen", datetime.now(UTC).isoformat(), job_id),
            )
            return False
        decision = Besluit.model_validate_json(str(row["payload"]))
        profile_raw: Any = json.loads(str(row["profiel_inst"]))
        if not isinstance(profile_raw, dict):
            raise ExecutionError("profielinstellingen zijn ongeldig")
        profile = {**STANDAARD, **profile_raw}
        database.execute(
            """
            UPDATE job SET bron_probe=?,bron_grootte=?,bron_mtime=?,bron_bestandsmetadata=?,
                           bron_device=?,bron_inode=? WHERE id=?
            """,
            (
                json.dumps(source_probe, ensure_ascii=False),
                source_stat.st_size,
                source_stat.st_mtime,
                metadata.to_json(),
                source_stat.st_dev,
                source_stat.st_ino,
                job_id,
            ),
        )
        quality_frames = await capture_comparison_frames(
            database,
            settings,
            job_id,
            source,
            None,
            float(source_probe["duration"]),
        )
        encode_source = source
        if decision.dv7_omzetten:
            if shutil.which("dovi_tool") is None:
                raise ExecutionError("dovi_tool ontbreekt; profiel 7 wordt niet half uitgevoerd")
            steps, prepared = commandos.dv7_naar_81(
                str(source), str(source_probe.get("fps") or "24000/1001"), str(work_dir)
            )
            for step in steps:
                if not await _run_process(
                    database,
                    job_id,
                    step,
                    log_dir / f"job-{job_id}.log",
                    float(source_probe["duration"]),
                ):
                    raise ExecutionError("Dolby Vision-profiel-7-voorbewerking faalde")
            encode_source = Path(prepared)

        if command_override is not None:
            arguments = command_override
        elif decision.actie == "opschonen":
            arguments = commandos.opschonen(
                source_probe, decision, str(encode_source), str(partial)
            )
        elif decision.encoder == "av1_qsv":
            arguments = commandos.encode_gpu(
                source_probe, decision, profile, str(encode_source), str(partial)
            )
        else:
            arguments = commandos.encode_cpu(
                source_probe, decision, profile, str(encode_source), str(partial)
            )
        if command_override is None and arguments and arguments[0] == "ffmpeg":
            # Een .deel-suffix is bewust niet als mediasuffix herkenbaar; geef
            # de muxer daarom expliciet op en hernoem pas na encode-succes.
            arguments = [*arguments[:-1], "-f", "matroska", arguments[-1]]
        succeeded = await _run_process(
            database,
            job_id,
            arguments,
            log_dir / f"job-{job_id}.log",
            float(source_probe["duration"]),
        )
        if not succeeded:
            abort_row = database.fetch_one(
                "SELECT afbreken_gevraagd FROM job WHERE id=?", (job_id,)
            )
            if abort_row is not None and int(abort_row[0]):
                database.execute(
                    """
                    UPDATE job SET status='afgebroken',fout='door gebruiker afgebroken',klaar_op=?
                    WHERE id=?
                    """,
                    (datetime.now(UTC).isoformat(), job_id),
                )
                shutil.rmtree(cache_dir)
                return False
            raise ExecutionError("encoderproces faalde")
        if not partial.is_file():
            raise ExecutionError("encoder meldde succes maar resultaat ontbreekt")
        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        partial.replace(result_path)

        if decision.herstel_hdr_na:
            restored = cache_dir / f"{source.stem}.hdr.mkv"
            hdr_command = commandos.herstel_hdr(
                source_probe["md_ruw"],
                source_probe["content_light"],
                str(result_path),
                str(restored),
            )
            if not await _run_process(
                database,
                job_id,
                hdr_command,
                log_dir / f"job-{job_id}.log",
                float(source_probe["duration"]),
            ):
                raise ExecutionError("HDR10-metadata terugmuxen faalde")
            restored.replace(result_path)

        database.execute("UPDATE job SET status='verifieren' WHERE id=?", (job_id,))
        verification = verify_result(source_probe, result_path, source_stat.st_size, decision)
        if verification_hook is not None:
            verification = verification_hook(verification)
        result_probe = verification.result_probe or {}
        database.execute(
            """
            INSERT OR REPLACE INTO verificatie(
                job_id,hdr_bron,hdr_resultaat,dv_bron,dv_resultaat,md_gelijk,
                audio_bron,audio_resultaat,attachments_bron,attachments_resultaat,
                geslaagd,toelichting
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                int(bool(source_probe.get("is_hdr10"))),
                int(bool(result_probe.get("is_hdr10"))),
                int(source_probe.get("dv_profile") or 0),
                int(result_probe.get("dv_profile") or 0),
                int(verification.mastering_equal),
                len(source_probe.get("audio") or []),
                len(result_probe.get("audio") or []),
                int(source_probe.get("attachments") or 0),
                int(result_probe.get("attachments") or 0),
                int(verification.passed),
                verification.reason,
            ),
        )
        if not verification.passed:
            raise ExecutionError(f"verificatie mislukt: {verification.reason}")

        quality_frames = await capture_comparison_frames(
            database,
            settings,
            job_id,
            source,
            result_path,
            float(source_probe["duration"]),
        )
        if quality_frames["bron"] or quality_frames["resultaat"]:
            database.execute(
                """
                INSERT INTO kwaliteitsmeting(
                    job_id,profiel_id,soort,start_seconde,duur_seconden,
                    bron_frame,resultaat_frame,vmaf_score,gemaakt_op
                ) VALUES (?,?,'vergelijking',?,0,?,?,?,?)
                """,
                (
                    job_id,
                    int(row["profiel_id"]),
                    float(source_probe["duration"]) / 2,
                    quality_frames["bron"],
                    quality_frames["resultaat"],
                    quality_frames["vmaf"],
                    datetime.now(UTC).isoformat(),
                ),
            )

        atomic_replace(database, job_id, source, result_path, metadata)
        final_size = source.stat().st_size
        database.execute(
            """
            UPDATE job SET status='klaar',resultaat_pad=?,resultaat_grootte=?,percent=100,
                           klaar_op=?,proces_pid=NULL WHERE id=?
            """,
            (str(source), final_size, datetime.now(UTC).isoformat(), job_id),
        )
        database.execute(
            """
            INSERT INTO besparing(job_id,library_id,bytes_voor,bytes_na,dag)
            VALUES (?,?,?,?,date('now'))
            """,
            (job_id, int(row["library_id"]), source_stat.st_size, final_size),
        )
        await refresh_after_replacement(database, settings, source, job_id)
        shutil.rmtree(cache_dir)
        return True
    except (ExecutionError, MetadataError, ReplacementError, OSError, ValueError) as exc:
        record_failure(database, job_id, str(exc), type(exc).__name__)
        return False
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        database.execute("UPDATE job SET cache_reservering=0 WHERE id=?", (job_id,))
