# Fase 0 — Scaffold (Ubuntu 26.04, native)

**Doel:** een native Ubuntu-installatie die bij de volgende herstart
automatisch terugkomt, een database die migreert, en een healthcheck die groen
is. Nog geen media, nog geen UI.

**Duur:** 1-2 uur.

---

## Lees eerst

- `spec/01-architectuur.md`
- `spec/03-datamodel.md`
- `spec/07-bediening-en-beheer.md`
- `spec/08-opslag-herstel-en-schakelaars.md`
- `AGENTS.md`
- `install/README.md`

## Opdracht

Zet het project op:

```
backend/
  app/
    __init__.py
    main.py            FastAPI-app, /health
    config.py          instellingen uit omgevingsvariabelen
    db.py              SQLite-verbinding, WAL, migraties
    migrations/
      001_init.sql     het schema uit spec/03-datamodel.md
    media/             hierin komt straks de startcode
  tests/
  pyproject.toml
  .venv/               niet in git
frontend/              (leeg in deze fase, alleen de map)
systemd/
  av1-pipeline-backend.service
  av1-pipeline-frontend.service
  av1-pipeline.env.example
install/
  installeer-ubuntu-26.04.sh
testdata/              (gegenereerd, in .gitignore)
```

**Backend:**

- FastAPI met `/health` die teruggeeft: versie, of de database bereikbaar is,
  of `ffmpeg`, `ffprobe`, `mkvmerge` en `dovi_tool` op het PATH staan, welke
  ffmpeg-versie het is en of `libsvtav1`, `av1_qsv` en de `-dolbyvision`-optie
  aanwezig zijn, en of `/dev/dri/renderD128` leesbaar en beschrijfbaar is.
- SQLite met WAL en `foreign_keys=ON`.
- Migraties: genummerde `.sql`-bestanden, een `schema_versie`-tabel, bij het
  starten toepassen wat nog niet is toegepast.
- Instellingen uit `/etc/av1-pipeline.env`, met bruikbare standaarden in
  `systemd/av1-pipeline.env.example`.
- Maak een eerste migratie voor de algemene instellingen en bewaar geheime
  integratiesleutels alleen versleuteld. De sleutel om ze te ontsleutelen komt
  uit een root-leesbaar bestand buiten de repository, niet uit de database.
- Houd `config` en SQLite vast onder `PIPELINE_ROOT` op de systeemschijf. Zet
  `cache`, `work` en standaard `backups` daar ook neer, met een harde vrije-
  ruimtebodem. Ondersteun later een vooraf gemounte afzonderlijke werkruimte
  via één gevalideerde opslagkeuze; formatteer of mount nooit automatisch.
- Maak de migraties voor `functie`, `vervang_transactie`, `auditlog` en
  `backup_status` meteen aan, ook als de bijbehorende opties nog uit staan.

## Native Ubuntu-installatie

Dit project draait rechtstreeks op **Ubuntu Server 26.04 LTS**. Gebruik geen
Docker, geen Compose en geen container-entrypoint.

- Gebruik de starter in `install/` om de APT-pakketten en de servicegebruiker
  te installeren. De gebruiker `av1pipeline` moet lid zijn van de groepen
  `render` en `video`; dat is nodig voor Intel QSV via `/dev/dri/renderD128`.
- Installeer Python-dependencies in `backend/.venv`; draai nooit `pip` in het
  systeem-Python.
- Maak twee systemd-units: één voor Uvicorn/FastAPI en één voor de Next.js
  frontend. Laat beide bij boot starten en bij een fout herstarten.
- De backend draait als `av1pipeline`; geef de frontend geen rechtstreekse
  toegang tot bibliotheek of opslagmappen.
- Gebruik de absolute paden uit `/etc/av1-pipeline.env`: `/library` en
  `/srv/av1-pipeline/{cache,work,config,backups}`.
  De cache staat buiten `/library`.

### Media-stack die verplicht is

Ubuntu 26.04 levert FFmpeg 8, dus ruim boven de vereiste 7.1. Installeer:

```
ffmpeg mkvtoolnix mediainfo
intel-media-va-driver-non-free libvpl2 libmfx-gen1.2 vainfo
python3 python3-venv python3-pip ca-certificates curl
```

`libvpl2` is alleen de oneVPL-dispatcher; **`libmfx-gen1.2` is de GPU-runtime**
en mag niet ontbreken. `intel-gpu-tools` is optioneel voor
`intel_gpu_top`-monitoring. `dovi_tool` is alleen nodig voor Dolby Vision
profiel 7; installeer een vaste release en leg de checksum vast. De standaard
`dv_modus=skip` werkt zonder dat gereedschap.

Controleer vóór je echte media verwerkt:

```bash
ffmpeg -hide_banner -encoders | grep -E 'libsvtav1|av1_qsv'
ffmpeg -hide_banner -h encoder=libsvtav1 | grep -i dolbyvision
vainfo
test -rw /dev/dri/renderD128
```

Ontbreekt `av1_qsv` of is de render node niet toegankelijk, dan is de
GPU-baan niet klaar. Val niet stilzwijgend terug op de CPU: maak `/health`
eerlijk rood en los eerst de host-stack op.

## Acceptatietest

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now av1-pipeline-backend
curl -s localhost:8000/health | jq

ffmpeg -version | head -1
ffmpeg -h encoder=libsvtav1 | grep -i dolby
systemctl status av1-pipeline-backend --no-pager
```

De healthcheck moet geldige JSON met `"database": "ok"` geven. Ontbrekend
optioneel gereedschap mag `false` zijn — het moet het alleen eerlijk melden.

Herstart daarna de machine. De backend moet vanzelf terugkomen en migraties
moeten niet dubbel draaien.

## Niet doen

- Geen Docker of Docker Compose toevoegen.
- Geen ORM (SQLAlchemy, SQLModel). Schrijf gewone SQL.
- Geen Alembic; genummerde `.sql`-bestanden zijn genoeg.
- Geen rootservice voor de app: alleen de eenmalige installatie gebruikt root.
