from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from app.uitvoerder import create_job_from_decision


@pytest.fixture
def api_case(tmp_path: Path) -> Iterator[tuple[TestClient, Database, Settings, Path]]:
    pipeline = tmp_path / "pipeline"
    pipeline.mkdir()
    roots = {name: pipeline / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    library_root = tmp_path / "library"
    media = library_root / "Films"
    media.mkdir(parents=True)
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", media / "Film.mkv")
    configured = replace(
        settings,
        library_root=library_root,
        pipeline_root=pipeline,
        workspace_root=pipeline,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
        secret_key_file=roots["config"] / "secret.key",
        storage_config_path=roots["config"] / "opslag.json",
        storage_mode="systeemschijf",
        backup_location="systeemschijf",
        system_device=pipeline.stat().st_dev,
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


def _planned_job(database: Database, library_id: int) -> int:
    scan_library(database, library_id)
    decision = database.fetch_one(
        "SELECT id FROM besluit WHERE actie IN ('hercoderen','opschonen') ORDER BY id LIMIT 1"
    )
    assert decision is not None
    decision_id = int(decision["id"])
    database.execute(
        "UPDATE besluit SET status='goedgekeurd' WHERE id=?", (decision_id,)
    )
    return create_job_from_decision(database, decision_id)


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
        "/api/opslag",
        "/api/opslag/controle",
        "/api/lan-toegang",
        "/api/ai-controller",
        "/api/ai-controller/analyses",
        "/api/ai-controller/statistieken",
        "/api/functies",
        "/api/systeem/qsv-test",
        "/api/systeem/wachtrijherstel",
        "/api/systeem/diagnose",
        "/api/systeem/mounts",
        "/api/kwaliteit/dolby-vision",
        "/api/backups/nu",
    }
    assert expected <= set(paths)
    assert client.get("/docs").status_code == 200


def test_library_scanschema_en_status_zijn_via_api_bedienbaar(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    database.execute(
        "INSERT INTO besparing(library_id,bytes_voor,bytes_na,dag) "
        "VALUES (?,?,?,date('now'))",
        (library_id, 10_000, 6_000),
    )

    library = next(
        item for item in client.get("/api/libraries").json() if item["id"] == library_id
    )
    assert library["scan_interval"] == 86400
    assert library["scan_tijd"] == "03:00"
    assert library["volgende_scan"] is not None
    assert library["stabiliteitswachtrij"] == 0
    assert library["bespaard_bytes"] == 4_000
    assert library["verwerkte_bron_bytes"] == 10_000

    response = client.patch(
        f"/api/libraries/{library_id}",
        json={"scan_interval": 0, "scan_tijd": "04:30"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["volgende_scan"] is None
    assert client.patch(
        f"/api/libraries/{library_id}", json={"scan_interval": 1234}
    ).status_code == 422


def test_library_verwijderen_ruimt_pipelinehistorie_op_en_laat_media_staan(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, configured, media = api_case
    library_id = _add_library(client, database, media)
    source = media / "Film.mkv"
    source_contents = source.read_bytes()
    job_id = _planned_job(database, library_id)
    job = database.fetch_one(
        "SELECT besluit_id,bestand_id FROM job WHERE id=?", (job_id,)
    )
    assert job is not None
    decision_id = int(job["besluit_id"])
    file_id = int(job["bestand_id"])

    for root in (configured.cache_root, configured.work_root):
        directory = root / str(job_id)
        directory.mkdir()
        (directory / "tijdelijk.mkv").write_bytes(b"tijdelijk")
    quality = configured.config_root / "kwaliteit" / str(job_id)
    quality.mkdir(parents=True)
    (quality / "vergelijking.png").write_bytes(b"afbeelding")
    database.execute(
        "INSERT INTO verificatie(job_id,geslaagd,toelichting) VALUES (?,1,'test')",
        (job_id,),
    )
    database.execute(
        "INSERT INTO kwaliteitsmeting(job_id,bestand_id,soort,gemaakt_op) "
        "VALUES (?,?,'vmaf',?)",
        (job_id, file_id, datetime.now(UTC).isoformat()),
    )
    database.execute(
        "INSERT INTO logregel(job_id,niveau,tekst,tijd) VALUES (?,'info','test',?)",
        (job_id, datetime.now(UTC).isoformat()),
    )
    database.execute(
        "INSERT INTO besparing(job_id,library_id,bytes_voor,bytes_na,dag) "
        "VALUES (?,?,100,80,date('now'))",
        (job_id, library_id),
    )
    analysis_id = database.execute_insert(
        """
        INSERT INTO ai_analyse(
            soort,fingerprint,library_id,besluit_id,job_id,provider,model,
            prompt_versie,modus,invoer_hash,gemaakt_op
        ) VALUES ('besluitgroep','wissen-test',?,?,?,'openai','test','1',
                  'schaduw','hash',?)
        """,
        (library_id, decision_id, job_id, datetime.now(UTC).isoformat()),
    )
    database.execute(
        "UPDATE besluit SET ai_analyse_id=? WHERE id=?", (analysis_id, decision_id)
    )
    database.execute(
        "INSERT INTO job_poging(job_id,poging,status,ai_analyse_id) "
        "VALUES (?,1,'mislukt',?)",
        (job_id, analysis_id),
    )

    refused = client.request(
        "DELETE",
        f"/api/libraries/{library_id}",
        json={"bevestiging": "verwijderen"},
    )
    assert refused.status_code == 409
    assert database.fetch_one("SELECT id FROM library WHERE id=?", (library_id,))

    removed = client.request(
        "DELETE",
        f"/api/libraries/{library_id}",
        json={"bevestiging": "BIBLIOTHEEK VERWIJDEREN"},
    )

    assert removed.status_code == 200, removed.text
    assert removed.json()["bron_ongemoeid"] is True
    assert removed.json()["mediapad_verwijderd"] is False
    assert removed.json()["pipeline_records"] == {
        "bestanden": 1,
        "besluiten": 1,
        "jobs": 1,
        "scans": 1,
        "ai_analyses": 1,
    }
    assert source.is_file()
    assert source.read_bytes() == source_contents
    assert media.is_dir()
    assert database.fetch_one("SELECT id FROM library WHERE id=?", (library_id,)) is None
    for table in (
        "bestand",
        "besluit",
        "job",
        "verificatie",
        "kwaliteitsmeting",
        "vervang_transactie",
        "logregel",
        "besparing",
        "scan_run",
        "ai_analyse",
        "job_poging",
    ):
        count = database.fetch_one(f"SELECT COUNT(*) AS aantal FROM {table}")
        assert count is not None and int(count["aantal"]) == 0
    assert not (configured.cache_root / str(job_id)).exists()
    assert not (configured.work_root / str(job_id)).exists()
    assert not quality.exists()
    audit = database.fetch_one(
        "SELECT oud FROM auditlog WHERE soort='bibliotheek' AND onderwerp='verwijderd'"
    )
    assert audit is not None
    assert json.loads(str(audit["oud"]))["bron_ongemoeid"] is True

    # Dezelfde echte map kan na administratieve opschoning opnieuw worden toegevoegd.
    replacement_id = _add_library(client, database, media)
    replacement = database.fetch_one(
        "SELECT pad FROM library WHERE id=?", (replacement_id,)
    )
    assert replacement is not None and str(replacement["pad"]) == str(media)


def test_library_verwijderen_weigert_actieve_job_en_behoudt_registratie(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    job_id = _planned_job(database, library_id)
    database.execute("UPDATE job SET status='bezig' WHERE id=?", (job_id,))

    response = client.request(
        "DELETE",
        f"/api/libraries/{library_id}",
        json={"bevestiging": "BIBLIOTHEEK VERWIJDEREN"},
    )

    assert response.status_code == 409
    assert f"#{job_id}" in response.text
    library = database.fetch_one("SELECT actief FROM library WHERE id=?", (library_id,))
    assert library is not None and int(library["actief"]) == 1
    assert (media / "Film.mkv").is_file()


def test_ai_controller_vereist_afgeschermde_activering_en_bewaart_geheim(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, _database, _configured, _media = api_case
    status_response = client.get("/api/ai-controller")
    assert status_response.status_code == 200
    assert status_response.json()["ingeschakeld"] is False
    assert status_response.json()["modus"] == "schaduw"

    denied = client.patch(
        "/api/ai-controller",
        json={
            "ingeschakeld": True,
            "modus": "actief",
            "maandbudget_usd": 10,
            "api_sleutel": "sk-test-dit-is-lang-genoeg",
        },
    )
    assert denied.status_code == 409

    shadow = client.patch(
        "/api/ai-controller",
        json={
            "ingeschakeld": True,
            "modus": "schaduw",
            "maandbudget_usd": 2.5,
            "api_sleutel": "sk-test-dit-is-lang-genoeg",
        },
    )
    assert shadow.status_code == 200, shadow.text
    assert shadow.json()["api_sleutel_ingesteld"] is True
    assert "sk-test" not in shadow.text
    assert client.patch(
        "/api/functies/ai_controller",
        json={"ingeschakeld": True, "configuratie": {}},
    ).status_code == 409


def test_ai_statistieken_aggregeren_lokale_dagen_en_voorstelvelden(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, _media = api_case
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    proposal = json.dumps(
        {
            "classificatie": "overeenkomstig",
            "actie": "besluit_goedkeuren",
            "redencodes": ["sdr_veilig"],
            "bewijsvelden": ["codec", "nlink"],
            "vertrouwen": 0.9,
        }
    )
    for made_at, classification, executed, input_tokens, output_tokens, cost in (
        (yesterday, "geblokkeerd", 0, 100, 20, 44),
        (now, "overeenkomstig", 1, 200, 30, 76),
    ):
        database.execute_insert(
            """
            INSERT INTO ai_analyse(
                soort,fingerprint,provider,model,prompt_versie,modus,invoer_hash,
                classificatie,voorgestelde_actie,voorstel,policy_uitkomst,
                uitgevoerd,input_tokens,output_tokens,kosten_microdollar,gemaakt_op
            ) VALUES ('besluitgroep',?,'openai','gpt-5.6-luna','1','actief','hash',
                      ?,'besluit_goedkeuren',?,'testpolicy',?,?,?,?,?)
            """,
            (
                f"fingerprint-{made_at.isoformat()}",
                classification,
                proposal,
                executed,
                input_tokens,
                output_tokens,
                cost,
                made_at.isoformat(),
            ),
        )

    response = client.get("/api/ai-controller/statistieken?dagen=30")
    assert response.status_code == 200, response.text
    overview = response.json()
    assert overview["tijdzone"] == "Europe/Amsterdam"
    assert len(overview["per_dag"]) == 30
    assert overview["totaal"]["beslissingen"] == 2
    assert overview["totaal"]["uitgevoerd"] == 1
    assert overview["totaal"]["input_tokens"] == 300
    assert overview["totaal"]["kosten_usd"] == 0.00012

    analyses = client.get("/api/ai-controller/analyses?limit=1").json()
    assert analyses[0]["redencodes"] == ["sdr_veilig"]
    assert analyses[0]["bewijsvelden"] == ["codec", "nlink"]
    assert analyses[0]["vertrouwen"] == 0.9
    assert analyses[0]["kosten_usd"] == 0.000076


def test_hdr10_ai_vrijgave_blijft_dicht_zonder_metadata_poort(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, _database, _configured, _media = api_case
    response = client.patch(
        "/api/ai-controller",
        json={
            "ingeschakeld": True,
            "modus": "schaduw",
            "maandbudget_usd": 10,
            "hdr10_vrijgegeven": True,
            "hdr10_bevestiging": "HDR10-METADATA-AUTONOMIE",
        },
    )

    assert response.status_code == 409
    assert "CPU en QSV" in response.text


def test_dolby_vision_overzicht_toont_bronnen_voor_verificatie(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    dv8_file = database.execute_insert(
        """
        INSERT INTO bestand(library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op)
        VALUES (?,?,?,?,1,'ok',?,CURRENT_TIMESTAMP)
        """,
        (
            library_id,
            str(media / "DV8.mkv"),
            1234,
            0.0,
            json.dumps({"dv_profile": 8, "dv_compat": 1, "is_hdr10": True}),
        ),
    )
    dv8_decision = database.execute_insert(
        """
        INSERT INTO besluit(
            bestand_id,actie,baan,encoder,reden,payload,status,gemaakt_op
        ) VALUES (?,'hercoderen','cpu','libsvtav1','DV profiel 8 naar AV1 profiel 10',
                  '{}','ingepland',CURRENT_TIMESTAMP)
        """,
        (dv8_file,),
    )
    dv8_job = database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
        (dv8_decision, dv8_file),
    )
    dv5_file = database.execute_insert(
        """
        INSERT INTO bestand(library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op)
        VALUES (?,?,?,?,1,'ok',?,CURRENT_TIMESTAMP)
        """,
        (
            library_id,
            str(media / "DV5.mkv"),
            2345,
            0.0,
            json.dumps({"dv_profile": 5, "dv_compat": 0, "is_hdr10": False}),
        ),
    )
    database.execute_insert(
        """
        INSERT INTO besluit(
            bestand_id,actie,baan,encoder,reden,payload,status,gemaakt_op
        ) VALUES (?,'overslaan',NULL,NULL,
                  'DV profiel 5: geen HDR10-terugval, nooit aanraken',
                  '{}','goedgekeurd',CURRENT_TIMESTAMP)
        """,
        (dv5_file,),
    )

    response = client.get("/api/kwaliteit/dolby-vision")

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["totaal"] == 2
    assert result["profielen"] == {"5": 1, "7": 0, "8": 1, "overig": 0}
    by_file = {item["bestand_id"]: item for item in result["items"]}
    assert by_file[dv8_file]["job_id"] == dv8_job
    assert by_file[dv8_file]["controle_status"] == "wacht_op_resultaat"
    assert by_file[dv5_file]["controle_status"] == "veilig_overgeslagen"


def test_algemeen_wachtrijherstel_is_begrensd_en_bevestigd(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, configured, media = api_case
    library_id = _add_library(client, database, media)
    job_id = _planned_job(database, library_id)
    source = media / "Film.mkv"
    source_before = source.read_bytes()
    database.execute(
        "UPDATE job SET status='bezig',proces_pid=999999,cache_reservering=123 WHERE id=?",
        (job_id,),
    )
    cache_dir = configured.cache_root / str(job_id)
    work_dir = configured.work_root / str(job_id)
    cache_dir.mkdir()
    work_dir.mkdir()
    (cache_dir / "Film.mkv.deel").write_bytes(b"tijdelijk")
    unknown = configured.cache_root / "niet-automatisch-verwijderen"
    unknown.mkdir()

    report = client.get("/api/systeem/wachtrijherstel")
    assert report.status_code == 200
    assert report.json()["weesjobs"] == [job_id]
    assert str(unknown) in report.json()["onbekende_paden"]
    refused = client.post(
        "/api/systeem/wachtrijherstel", json={"bevestiging": "reset"}
    )
    assert refused.status_code == 409

    recovered = client.post(
        "/api/systeem/wachtrijherstel",
        json={"bevestiging": "WACHTRIJ VEILIG HERSTELLEN"},
    )

    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["herstelde_jobs"] == [job_id]
    assert not cache_dir.exists() and not work_dir.exists()
    assert unknown.is_dir()
    assert source.read_bytes() == source_before
    job = database.fetch_one("SELECT status FROM job WHERE id=?", (job_id,))
    assert job is not None and job["status"] == "wacht"


def test_opnieuw_proberen_ruimt_eerst_oude_tijdelijke_output_op(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, configured, media = api_case
    library_id = _add_library(client, database, media)
    job_id = _planned_job(database, library_id)
    database.execute(
        "UPDATE job SET status='mislukt',fout='testfout',foutsoort='TestError' WHERE id=?",
        (job_id,),
    )
    cache_dir = configured.cache_root / str(job_id)
    work_dir = configured.work_root / str(job_id)
    cache_dir.mkdir()
    work_dir.mkdir()
    (cache_dir / "Film.mkv.deel").write_bytes(b"onvolledig")

    response = client.post(f"/api/jobs/{job_id}/opnieuw")

    assert response.status_code == 200, response.text
    assert not cache_dir.exists() and not work_dir.exists()
    job = database.fetch_one(
        "SELECT status,cache_reservering FROM job WHERE id=?", (job_id,)
    )
    assert job is not None and job["status"] == "wacht"
    assert job["cache_reservering"] == 0


def test_tweede_besluit_voor_zelfde_bestand_gebruikt_bestaande_job(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    first_job = _planned_job(database, library_id)
    decision = database.fetch_one(
        "SELECT b.*,f.id AS bestand_id FROM besluit b "
        "JOIN bestand f ON f.id=b.bestand_id ORDER BY b.id LIMIT 1"
    )
    assert decision is not None
    second_decision = database.execute_insert(
        """
        INSERT INTO besluit(bestand_id,actie,baan,encoder,reden,payload,status,gemaakt_op)
        VALUES (?,?,?,?,?,?,'goedgekeurd',CURRENT_TIMESTAMP)
        """,
        (
            int(decision["bestand_id"]),
            str(decision["actie"]),
            str(decision["baan"]),
            decision["encoder"],
            str(decision["reden"]),
            str(decision["payload"]),
        ),
    )

    second_job = create_job_from_decision(database, second_decision)

    assert second_job == first_job
    assert database.fetch_one("SELECT COUNT(*) FROM job")[0] == 1


def test_worker_api_en_totale_encodegrens_zijn_zonder_herstart_insteldbaar(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, _media = api_case
    workers = client.get("/api/workers")
    assert workers.status_code == 200
    assert all(worker["veilige_max_workers"] == 1 for worker in workers.json())
    assert all(worker["totaal_max_actieve_jobs"] == 1 for worker in workers.json())
    assert all(worker["geheugen_totaal_bytes"] > 0 for worker in workers.json())

    configured = client.patch(
        "/api/instellingen", json={"max_gelijktijdige_encodes": 3}
    )
    worker = client.patch("/api/workers/cpu", json={"aantal": 3})

    assert configured.status_code == 200
    assert configured.json()["max_gelijktijdige_encodes"] == 3
    assert worker.status_code == 200
    assert worker.json()["aantal"] == 3
    assert database.fetch_one(
        "SELECT max_gelijktijdige_encodes FROM instelling WHERE id=1"
    )[0] == 3


def test_canary_rapport_en_vrijgave_zijn_per_library_bevestigd(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, media = api_case
    library_id = _add_library(client, database, media)
    job_id = _planned_job(database, library_id)
    source_size = (media / "Film.mkv").stat().st_size
    database.execute(
        """
        UPDATE job SET status='klaar',bron_grootte=?,resultaat_grootte=?,
                       gestart_op=CURRENT_TIMESTAMP,klaar_op=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (source_size, source_size // 2, job_id),
    )

    report = client.get(f"/api/libraries/{library_id}/canary")
    assert report.status_code == 200
    assert report.json()["klaar"] == 1
    assert report.json()["kan_vrijgeven"] is True
    refused = client.post(
        f"/api/libraries/{library_id}/canary-vrijgeven",
        json={"bevestiging": "vrijgeven"},
    )
    assert refused.status_code == 409

    released = client.post(
        f"/api/libraries/{library_id}/canary-vrijgeven",
        json={"bevestiging": "CANARY-BATCH VRIJGEVEN"},
    )

    assert released.status_code == 200, released.text
    assert released.json()["status"] == "vrijgegeven"


def test_library_accepteert_vrije_mediamap_weigert_pipelinepad_en_scant_asynchroon(
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
    accepted = client.post(
        "/api/libraries",
        json={"naam": "Buiten", "pad": str(outside), "profiel_id": profile["id"]},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["bron_type"] == "lokaal"
    assert accepted.json()["bron_controle"]["geslaagd"] is True

    rejected = client.post(
        "/api/libraries",
        json={
            "naam": "Pipeline zelf",
            "pad": str(_configured.pipeline_root),
            "profiel_id": profile["id"],
        },
    )
    assert rejected.status_code == 422


def test_library_slaat_smb_bron_op_zonder_accountgeheim(
    api_case: tuple[TestClient, Database, Settings, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database, _configured, _media = api_case
    mounted = tmp_path / "smb-mount" / "Media" / "test"
    mounted.mkdir(parents=True)
    monkeypatch.setattr("app.api.ensure_smb_mount", lambda *_args, **_kwargs: mounted)
    profile = database.fetch_one("SELECT id FROM profiel LIMIT 1")
    assert profile is not None

    response = client.post(
        "/api/libraries",
        json={
            "naam": "TrueNAS test",
            "locatie": "smb://TrueNAS.local/jelly/Media/test",
            "profiel_id": profile["id"],
            "smb_gebruikersnaam": "media-account",
            "smb_wachtwoord": "zeer-geheim-wachtwoord",
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["bron_type"] == "smb"
    assert payload["bron_uri"] == "smb://truenas.local/jelly/Media/test"
    assert payload["pad"] == str(mounted)
    assert "zeer-geheim-wachtwoord" not in response.text
    row = database.fetch_one("SELECT * FROM library WHERE id=?", (payload["id"],))
    assert row is not None
    assert "zeer-geheim-wachtwoord" not in " ".join(str(value) for value in row)
    assert list(mounted.iterdir()) == []


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
        assert first["systeem"]["geheugen"]["totaal_bytes"] > 0
        assert 0 <= first["systeem"]["geheugen"]["percent"] <= 100
        assert "platform_power_w" in first["systeem"]["gpu"]
        assert "platform_power_beschikbaar" in first["systeem"]["gpu"]
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


def test_lan_toegang_vergt_account_hash_en_exacte_bevestiging(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, _configured, _media = api_case

    initial = client.get("/api/lan-toegang")
    assert initial.status_code == 200
    assert initial.json()["ingeschakeld"] is False
    assert client.get("/api/lan-toegang/autoriseer").status_code == 403

    rejected = client.patch(
        "/api/lan-toegang",
        json={
            "ingeschakeld": True,
            "gebruikersnaam": "transcode",
            "wachtwoord": "veilig-testwachtwoord",
            "bevestiging": "verkeerd",
        },
    )
    assert rejected.status_code == 409

    enabled = client.patch(
        "/api/lan-toegang",
        json={
            "ingeschakeld": True,
            "gebruikersnaam": "transcode",
            "wachtwoord": "veilig-testwachtwoord",
            "bevestiging": "LAN-TOEGANG INSCHAKELEN",
        },
    )
    assert enabled.status_code == 200
    assert enabled.json()["wachtwoord_ingesteld"] is True
    assert "veilig-testwachtwoord" not in json.dumps(enabled.json())

    stored = database.fetch_one("SELECT configuratie FROM functie WHERE sleutel='externe_toegang'")
    assert stored is not None
    configuration = json.loads(stored["configuratie"])
    assert configuration["wachtwoord_hash"].startswith("scrypt$")
    assert "veilig-testwachtwoord" not in stored["configuratie"]

    denied = client.get("/api/lan-toegang/autoriseer", auth=("transcode", "verkeerd-wachtwoord"))
    assert denied.status_code == 401
    authorized = client.get(
        "/api/lan-toegang/autoriseer", auth=("transcode", "veilig-testwachtwoord")
    )
    assert authorized.status_code == 204

    features = client.get("/api/functies").json()
    external = next(item for item in features if item["sleutel"] == "externe_toegang")
    assert "wachtwoord_hash" not in external["configuratie"]
    generic_update = client.patch(
        "/api/functies/externe_toegang",
        json={"ingeschakeld": False, "configuratie": {}},
    )
    assert generic_update.status_code == 409

    disabled = client.patch("/api/lan-toegang", json={"ingeschakeld": False})
    assert disabled.status_code == 200
    assert disabled.json()["ingeschakeld"] is False
    assert client.get("/api/lan-toegang/autoriseer").status_code == 403


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


def test_opslagkeuze_wordt_gecontroleerd_en_atomisch_bewaard(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, database, configured, _media = api_case
    current = client.get("/api/opslag")
    assert current.status_code == 200
    assert current.json()["modus"] == "systeemschijf"
    assert current.json()["systeemschijf_reserve_gb"] == 150

    choice = {
        "modus": "systeemschijf",
        "werkruimte_pad": str(configured.pipeline_root),
        "backups_op": "systeemschijf",
        "systeemschijf_reserve_gb": 175,
    }
    checked = client.post("/api/opslag/controle", json=choice)
    assert checked.status_code == 200, checked.text
    assert checked.json()["controle"] == "veilig toepasbaar"

    refused = client.patch("/api/opslag", json={**choice, "bevestiging": "bijna"})
    assert refused.status_code == 409
    applied = client.patch("/api/opslag", json={**choice, "bevestiging": "OPSLAG WIJZIGEN"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["systeemschijf_reserve_gb"] == 175
    assert configured.storage_config_path.is_file()
    assert '"systeemschijf_reserve_gb": 175' in configured.storage_config_path.read_text()
    assert all(
        row["gepauzeerd"] for row in database.fetch_all("SELECT gepauzeerd FROM worker_config")
    )


def test_health_wordt_direct_degraded_als_werkopslag_verdwijnt(
    api_case: tuple[TestClient, Database, Settings, Path],
) -> None:
    client, _database, configured, _media = api_case
    shutil.rmtree(configured.cache_root)

    api_health = client.get("/api/health")
    root_health = client.get("/health")
    storage = client.get("/api/opslag")

    assert api_health.status_code == 200
    assert api_health.json()["status"] == "degraded"
    assert root_health.json()["status"] == "degraded"
    assert "ontbreekt" in str(storage.json()["opslagfout"])
