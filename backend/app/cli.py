"""Beheer-CLI; analysecommando's wijzigen nooit een mediabestand."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import Database
from app.media import commandos
from app.media.beslissen import STANDAARD, Besluit, beslis
from app.media.probe import probe
from app.planner import Planner
from app.scanner import ScanError, scan_library
from app.uitvoerder import ExecutionError, create_job_from_decision, run_job


def _profile(path: str | None) -> dict[str, Any]:
    if path is None:
        return dict(STANDAARD)
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Een profielbestand moet een JSON-object bevatten")
    return {**STANDAARD, **value}


def _probe_or_exit(path: str) -> dict[str, Any]:
    result = probe(path)
    if result is None:
        raise ValueError(f"Niet te lezen als video: {path}")
    return result


def _command(info: dict[str, Any], decision: Besluit, profile: dict[str, Any]) -> list[str]:
    source = str(info["pad"])
    target = str(Path(source).with_name(f".{Path(source).name}.voorbeeld.mkv"))
    if decision.actie == "opschonen":
        return commandos.opschonen(info, decision, source, target)
    if decision.encoder == "libsvtav1":
        return commandos.encode_cpu(info, decision, profile, source, target)
    if decision.encoder == "av1_qsv":
        return commandos.encode_gpu(info, decision, profile, source, target)
    return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="av1-pipeline")
    subparsers = parser.add_subparsers(dest="opdracht", required=True)
    for name in ("probe", "beslis", "commando"):
        command = subparsers.add_parser(name)
        command.add_argument("bestand")
        if name in {"beslis", "commando"}:
            command.add_argument("profiel", nargs="?")
    scan = subparsers.add_parser("scan")
    scan.add_argument("library_id", type=int)
    scan.add_argument("--dry-run", action="store_true")
    decisions = subparsers.add_parser("besluiten")
    decisions.add_argument("library_id", type=int)
    decisions.add_argument("--status", default="nieuw")
    approve = subparsers.add_parser("goedkeuren")
    approve.add_argument("library_id", type=int)
    approve.add_argument("--actie", required=True)
    run = subparsers.add_parser("draai")
    run.add_argument("job_id", type=int)
    run_decision = subparsers.add_parser("draai-besluit")
    run_decision.add_argument("besluit_id", type=int)
    subparsers.add_parser("planner-start")
    subparsers.add_parser("wachtrij")
    pause = subparsers.add_parser("pauzeer")
    pause.add_argument("baan", choices=("cpu", "gpu"))
    return parser


def _catalog_command(args: argparse.Namespace) -> object:
    database = Database(settings)
    database.initialize()
    if args.opdracht == "scan":
        summary = scan_library(database, args.library_id, dry_run=args.dry_run)
        return {
            "library_id": summary.library_id,
            "dry_run": summary.dry_run,
            "gevonden": summary.gevonden,
            "nieuw": summary.nieuw,
            "gewijzigd": summary.gewijzigd,
            "verwijderd": summary.verwijderd,
            "hervat": summary.hervat,
            "items": [
                {
                    "pad": item.pad,
                    "categorie": item.categorie,
                    "filter_reden": item.filter_reden,
                    "actie": item.actie,
                    "besluit_reden": item.besluit_reden,
                    "nlink": item.nlink,
                }
                for item in summary.items
            ],
        }
    if args.opdracht == "besluiten":
        rows = database.fetch_all(
            """
            SELECT b.id,b.actie,b.baan,b.encoder,b.reden,b.status,f.pad,f.nlink
            FROM besluit b JOIN bestand f ON f.id=b.bestand_id
            WHERE f.library_id=? AND b.status=? ORDER BY b.id
            """,
            (args.library_id, args.status),
        )
        return [dict(row) for row in rows]
    if args.opdracht == "goedkeuren":
        if args.actie == "opruimen":
            raise ValueError("Opruimen vereist een aparte bevestiging via de API of interface")
        changed = database.execute(
            """
            UPDATE besluit SET status='goedgekeurd'
            WHERE status='nieuw' AND actie=? AND bestand_id IN
              (SELECT id FROM bestand WHERE library_id=? AND nlink=1)
            """,
            (args.actie, args.library_id),
        )
        return {"goedgekeurd": changed, "actie": args.actie}
    raise ValueError(f"Onbekend cataloguscommando: {args.opdracht}")


def _execution_command(args: argparse.Namespace) -> object:
    database = Database(settings)
    database.initialize()
    job_id = (
        create_job_from_decision(database, args.besluit_id)
        if args.opdracht == "draai-besluit"
        else args.job_id
    )
    succeeded = asyncio.run(run_job(database, settings, job_id))
    job = database.fetch_one(
        "SELECT id,status,fout,resultaat_grootte FROM job WHERE id=?", (job_id,)
    )
    return {"geslaagd": succeeded, "job": dict(job) if job is not None else None}


def _planner_command(args: argparse.Namespace) -> object:
    database = Database(settings)
    database.initialize()
    if args.opdracht == "wachtrij":
        rows = database.fetch_all(
            "SELECT id,baan,status,prioriteit,percent,fout FROM job "
            "ORDER BY baan,prioriteit DESC,id"
        )
        return [dict(row) for row in rows]
    if args.opdracht == "pauzeer":
        database.execute(
            "UPDATE worker_config SET gepauzeerd=1,pauze_reden='handmatig gepauzeerd' WHERE baan=?",
            (args.baan,),
        )
        return {"baan": args.baan, "gepauzeerd": True}
    planner = Planner(database, settings)
    try:
        asyncio.run(planner.run_forever())
    except KeyboardInterrupt:
        return {"gestopt": True}
    return {"gestopt": True}


def main(arguments: list[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    try:
        if args.opdracht in {"scan", "besluiten", "goedkeuren"}:
            print(json.dumps(_catalog_command(args), indent=2, ensure_ascii=False))
            return 0
        if args.opdracht in {"draai", "draai-besluit"}:
            print(json.dumps(_execution_command(args), indent=2, ensure_ascii=False))
            return 0
        if args.opdracht in {"planner-start", "wachtrij", "pauzeer"}:
            print(json.dumps(_planner_command(args), indent=2, ensure_ascii=False))
            return 0
        info = _probe_or_exit(args.bestand)
        if args.opdracht == "probe":
            output: object = info
        else:
            profile = _profile(args.profiel)
            decision = beslis(info, profile)
            output = decision.model_dump()
            if args.opdracht == "commando":
                output = _command(info, decision, profile)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ScanError, ExecutionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
