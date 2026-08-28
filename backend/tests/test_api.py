from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import TESTDATA
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.db import Database
from app.geheimen import SecretStore
from app.main import create_app
from app.scanner import scan_library
from app.systeem import GpuMonitor


@pytest.fixture
def api_case(tmp_path: Path) -> Iterator[tuple[TestClient, Database, Settings, Path]]:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    library_root = tmp_path / "library"
    media = library_root / "Films"
    media.mkdir(parents=True)
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", media / "Film.mkv")
    configured = replace(
        settings,
        library_root=library_root,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
        secret_key_file=roots["config"] / "secret.key",
    )
    configured.secret_key_file.write_bytes(b"api-test-sleutelmateriaal" * 4)
    database = Database(configured)
    application = create_app(
        configured,
        database,
        start_planner=False,
        validate_storage=False,
    )
    with TestClient(application) as client:
        yield client, database, configured, media


def _add_library(client: TestClient, database: Database, media: Path) -> int:
    profile = database.fetch_one("SELECT id FROM profiel ORDER BY id LIMIT 1")
    assert profile is not None
    response = client.post(
        "/api/libraries",
        json={"naam": "Films", "pad": str(media), "profiel_id": int(profile["id"])},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def test_openapi_bevat_alle_fase_vijf_routes(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, _database, _configured, _media = api_case
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/health",
        "/api/libraries",
        "/api/profielen",
        "/api/besluiten",
        "/api/jobs",
        "/api/workers",
        "/api/fouten",
        "/api/statistieken",
        "/api/instellingen",
        "/api/functies",
        "/api/systeem/qsv-test",
        "/api/systeem/diagnose",
        "/api/systeem/mounts",
        "/api/backups/nu",
    }
    assert expected <= set(paths)
    assert client.get("/docs").status_code == 200


def test_library_blijft_binnen_beheerde_root_en_scan_is_asynchroon(
    api_case: tuple[TestClient, Database, Settings, Path], tmp_path: Path
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)

    response = client.post(f"/api/libraries/{library_id}/droogloop")
    assert response.status_code == 202
    assert response.json()["droogloop"] is True
    assert response.json()["taak_id"]

    outside = tmp_path / "buiten"
    outside.mkdir()
    profile = database.fetch_one("SELECT id FROM profiel LIMIT 1")
    assert profile is not None
    rejected = client.post(
        "/api/libraries",
        json={"naam": "Buiten", "pad": str(outside), "profiel_id": profile["id"]},
    )
    assert rejected.status_code == 422


def test_besluiten_worden_pas_na_goedkeuring_jobs(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    summary = scan_library(database, library_id)
    assert summary.gevonden == 1
    assert database.fetch_one("SELECT id FROM job") is None

    grouped = client.get("/api/besluiten", params={"status": "nieuw"})
    assert grouped.status_code == 200
    assert grouped.json()["totaal"] == 1
    decision = database.fetch_one("SELECT id FROM besluit")
    assert decision is not None
    approved = client.post("/api/besluiten/goedkeuren", json={"ids": [int(decision["id"])]})
    assert approved.status_code == 200, approved.text
    assert len(approved.json()["job_ids"]) == 1
    job = database.fetch_one("SELECT status FROM job")
    assert job is not None and job["status"] == "wacht"


def test_opruimen_vergt_exacte_dubbele_bevestiging(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    scan_library(database, library_id)
    row = database.fetch_one(
        "SELECT b.id,f.grootte FROM besluit b JOIN bestand f ON f.id=b.bestand_id"
    )
    assert row is not None
    database.execute("UPDATE besluit SET actie='opruimen' WHERE id=?", (row["id"],))

    first = client.post("/api/besluiten/goedkeuren", json={"ids": [row["id"]]})
    assert first.status_code == 409
    detail = first.json()["detail"]
    assert detail["aantal"] == 1 and detail["bytes"] == row["grootte"]
    confirmed = client.post(
        "/api/besluiten/goedkeuren",
        json={
            "ids": [row["id"]],
            "bevestig_aantal": 1,
            "bevestig_bytes": row["grootte"],
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["job_ids"] == []


def test_besluitgroep_filter_keurt_geen_naburige_groep_goed(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    scan_library(database, library_id)
    first = database.fetch_one(
        "SELECT b.*,f.id AS bestand_id FROM besluit b JOIN bestand f ON f.id=b.bestand_id"
    )
    assert first is not None
    second_file = database.execute_insert(
        """
        INSERT INTO bestand(library_id,pad,grootte,mtime,nlink,categorie,gescand_op)
        VALUES (?,?,?,?,1,'ok',CURRENT_TIMESTAMP)
        """,
        (library_id, str(media / "Tweede.mkv"), 1234, 0.0),
    )
    second = database.execute_insert(
        """
        INSERT INTO besluit(bestand_id,actie,baan,encoder,reden,payload,status,gemaakt_op)
        VALUES (?,?,?,?,?,?,'nieuw',CURRENT_TIMESTAMP)
        """,
        (
            second_file,
            first["actie"],
            first["baan"],
            first["encoder"],
            "andere groep",
            first["payload"],
        ),
    )

    response = client.post(
        "/api/besluiten/goedkeuren",
        json={
            "filter": {
                "status": "nieuw",
                "actie": first["actie"],
                "baan": first["baan"],
                "encoder": first["encoder"],
                "reden": first["reden"],
                "library_id": library_id,
            }
        },
    )
    assert response.status_code == 200
    untouched = database.fetch_one("SELECT status FROM besluit WHERE id=?", (second,))
    assert untouched is not None and untouched["status"] == "nieuw"


def test_websocket_tick_en_logabonnement(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, configured, media = api_case
    library_id = _add_library(client, database, media)
    scan_library(database, library_id)
    decision = database.fetch_one("SELECT id FROM besluit")
    assert decision is not None
    client.post("/api/besluiten/goedkeuren", json={"ids": [decision["id"]]})
    job = database.fetch_one("SELECT id FROM job")
    assert job is not None
    log_dir = configured.config_root / "logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / f"job-{job['id']}.log").write_text("historisch\n", encoding="utf-8")

    with client.websocket_connect("/ws") as websocket:
        first = websocket.receive_json()
        assert first["type"] == "tick"
        assert {"workers", "systeem", "wachtrij"} <= set(first)
        assert set(first["wachtrij"]) == {"wachtend", "bezig", "klaar_vandaag", "fouten"}
        websocket.send_json({"actie": "volg", "job_id": job["id"]})
        tick_message = websocket.receive_json()
        assert tick_message["type"] == "tick"
        with (log_dir / f"job-{job['id']}.log").open("a", encoding="utf-8") as handle:
            handle.write("nieuwe regel\n")
        messages = [websocket.receive_json() for _ in range(2)]
        assert any(
            message.get("type") == "log" and message.get("tekst") == "nieuwe regel"
            for message in messages
        )


def test_gpu_monitor_mislukking_is_niet_fataal(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = GpuMonitor()
    monkeypatch.setattr("app.systeem.shutil.which", lambda _name: None)

    import asyncio

    asyncio.run(monitor.start())
    assert monitor.latest["beschikbaar"] is False
    assert "niet geïnstalleerd" in monitor.latest["fout"]


def test_backup_wordt_met_checksum_en_integriteitscontrole_getest(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, _database, _configured, _media = api_case
    made = client.post("/api/backups/nu")
    assert made.status_code == 201
    body = made.json()
    assert len(body["sha256"]) == 64
    checked = client.post(f"/api/backups/{body['id']}/controleer-herstel")
    assert checked.status_code == 200
    assert checked.json()["herstelbaar"] is True


def test_geheimen_komen_niet_via_instellingen_api(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, _media = api_case
    store = SecretStore(database, _configured)
    store.set("jellyfin_api_sleutel", "supergeheim")
    store.set("radarr_api_sleutel", "ookgeheim")
    text = json.dumps(client.get("/api/instellingen").json())
    assert "supergeheim" not in text
    assert "ookgeheim" not in text


def test_hardlink_vergt_beleid_en_twee_afzonderlijke_bevestigingen(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    scan_library(database, library_id)
    row = database.fetch_one("SELECT id FROM besluit")
    assert row is not None
    decision_id = int(row["id"])
    database.execute(
        """
        UPDATE bestand SET nlink=2 WHERE id=(SELECT bestand_id FROM besluit WHERE id=?);
        """,
        (decision_id,),
    )
    database.execute(
        "UPDATE besluit SET actie='overslaan',baan=NULL,encoder=NULL,reden='hardlink' WHERE id=?",
        (decision_id,),
    )

    default_rejected = client.patch(
        f"/api/besluiten/{decision_id}",
        json={"baan": "cpu", "encoder": "libsvtav1", "hardlink_bevestigd": True},
    )
    assert default_rejected.status_code == 409

    configured = client.patch(
        "/api/instellingen", json={"hardlink_modus": "verbreek_na_bevestiging"}
    )
    assert configured.status_code == 200
    enabled = client.patch(
        "/api/functies/hardlink_verbreken",
        json={
            "ingeschakeld": True,
            "configuratie": {},
            "bevestiging": "HARDLINKS VERBREKEN",
        },
    )
    assert enabled.status_code == 200
    per_file = client.patch(
        f"/api/besluiten/{decision_id}",
        json={"baan": "cpu", "encoder": "libsvtav1", "hardlink_bevestigd": True},
    )
    assert per_file.status_code == 200, per_file.text
    assert per_file.json()["verwachte_extra_bytes"] > 0

    first_approval = client.post("/api/besluiten/goedkeuren", json={"ids": [decision_id]})
    assert first_approval.status_code == 409
    final_approval = client.post(
        "/api/besluiten/goedkeuren",
        json={"ids": [decision_id], "hardlinks_bevestigd": True},
    )
    assert final_approval.status_code == 200
    assert len(final_approval.json()["job_ids"]) == 1


def test_hdr10plus_heeft_geen_forceerroute(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    scan_library(database, library_id)
    row = database.fetch_one(
        "SELECT b.id,f.probe FROM besluit b JOIN bestand f ON f.id=b.bestand_id"
    )
    assert row is not None
    probe_data = json.loads(str(row["probe"]))
    probe_data["hdr10plus"] = True
    database.execute(
        "UPDATE bestand SET probe=? WHERE id=(SELECT bestand_id FROM besluit WHERE id=?)",
        (json.dumps(probe_data), row["id"]),
    )
    response = client.patch(
        f"/api/besluiten/{row['id']}",
        json={"baan": "cpu", "encoder": "libsvtav1"},
    )
    assert response.status_code == 409
    assert "nooit geforceerd" in response.text


def test_cachegrenzen_worden_ook_bij_een_enkele_wijziging_gevalideerd(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, _database, _configured, _media = api_case
    response = client.patch("/api/instellingen", json={"cache_pauze_gb": 50})
    assert response.status_code == 422
