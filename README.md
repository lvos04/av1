# AV1 Pipeline

Een persoonlijke pipeline die een mediabibliotheek van circa 50 TB kleiner maakt:
overbodige audiosporen eruit, video naar AV1, zonder HDR10 of Dolby Vision te
slopen. Draait native op **Ubuntu Server 26.04 LTS**, op één machine met een
Intel Arc (GPU-baan) en een CPU (CPU-baan) die tegelijk werken. Geen Docker.

Eén gebruiker, eigen thuisnetwerk. Geen multi-tenant, geen microservices.

---

## Wat het doet

1. **Scannen** – de bibliotheek wordt incrementeel en volgens schema gescand.
   Elk bestand krijgt een deterministisch besluit: opschonen, encoderen op CPU
   of GPU, of overslaan met een reden.
2. **Goedkeuren** – jij keurt besluiten goed in de webinterface. Na een
   canary-fase mag een begrensde AI-controller uitsluitend veilige, door de
   Python-policy toegestane besluiten bevestigen. Alles wat afwijkt blijft op
   jou wachten.
3. **Uitvoeren** – twee workers draaien tegelijk: `av1_qsv` op Intel Arc voor
   SDR 1080p, `libsvtav1` op CPU voor 4K en HDR. Op hosts met minder dan
   24 GiB geheugen draait één mediajob tegelijk.
4. **Verifiëren en vervangen** – er wordt geëncodeerd naar de cache, geverifieerd
   tegen metadata van vóór de encode, en pas daarna wordt het origineel
   atomisch vervangen. Faalt een controle, dan blijft de bron onaangeraakt.
5. **Volgen** – een dashboard toont realtime fps, ETA, GPU- en CPU-belasting,
   bitrate, besparing, fouten en logs.

## Veiligheidsregels

Volledig in `spec/04-veiligheidsregels.md`. De kern:

- Besluiten en uitvoeren zijn gescheiden. AI kan de Python-policy nooit omzeilen.
- Het origineel wordt nooit overschreven vóór verificatie.
- Verificatie gebruikt metadata die op de job is vastgelegd, nooit het bronpad opnieuw.
- Bij twijfel overslaan. HDR10+ altijd overslaan. Hardlinks standaard overslaan.
- Dolby Vision staat standaard op `skip`; profiel 5 wordt nooit aangeraakt.
- Nooit `shell=True`.

De backend luistert alleen op `127.0.0.1:8000`. Bediening vanaf andere
computers loopt uitsluitend via de frontend-gateway op poort 3000, die
standaard uit staat en een apart wachtwoord vereist.

---

## Stack

| Laag | Technologie |
|---|---|
| Backend | Python 3.12+, FastAPI, SQLite (WAL), asyncio-workers |
| Frontend | Next.js (App Router), TypeScript strict, Tailwind, shadcn/ui, Recharts |
| Media | ffmpeg **7.1+** (SVT-AV1, av1_qsv), mkvmerge, dovi_tool 2.3.3 |
| Platform | Ubuntu Server 26.04 LTS, twee systemd-services |

Bewust niet gebruikt: Celery, Redis, Postgres, ORM, containers.

---

## Mappen

```
backend/         FastAPI-app, workers, scanner, verificatie, migraties, tests
frontend/        Next.js-dashboard en LAN-gateway (server.mjs)
install/         installatiescripts voor Ubuntu 26.04 en dovi_tool
systemd/         service-units en voorbeeld-omgevingsbestand
spec/            architectuur, domeinkennis, datamodel, veiligheidsregels, UI
prompts/         de twaalf bouwfasen (00 t/m 11) met acceptatietests
startcode/       vijf losse modules (probe, beslissen, commando's, filter, voortgang)
test/            generator voor korte testbestanden plus tests op de startcode
AGENTS.md        projectinstructies en projectgeheugen voor AI-agents
START-HIER.md    de oorspronkelijke bouwhandleiding
```

Lees `spec/02-domeinkennis.md` voordat je aan media-code werkt. Het beschrijft
vijftien valkuilen die allemaal stil falen: je ziet ze niet in de logs, maar op
je TV na vijfhonderd bestanden.

---

## Installatie op de host

Vereist: Ubuntu Server 26.04, een Intel-GPU met `/dev/dri/renderD128`, ffmpeg
7.1 of nieuwer met `libsvtav1` en `av1_qsv`, mkvmerge.

```bash
# 1. Project op de host plaatsen
sudo mkdir -p /opt/av1-pipeline
sudo cp -R . /opt/av1-pipeline

# 2. Systeemgebruikers, groepen, mappen, sleutelbestand, dovi_tool en services
sudo bash /opt/av1-pipeline/install/installeer-ubuntu-26.04.sh /opt/av1-pipeline

# 3. Backend
cd /opt/av1-pipeline/backend
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"

# 4. Frontend
cd /opt/av1-pipeline/frontend
npm ci
npm run build

# 5. Omgeving
sudo cp /opt/av1-pipeline/systemd/av1-pipeline.env.example /etc/av1-pipeline.env
sudo nano /etc/av1-pipeline.env   # LIBRARY_ROOT en paden controleren

# 6. Starten
sudo systemctl enable --now av1-pipeline-backend av1-pipeline-frontend
```

Open daarna lokaal `http://127.0.0.1:3000`. Alle verdere instellingen staan in
de UI onder **Instellingen**.

Optioneel, bij een bestaande installatie:

```bash
# LAN-toegang via de gateway op poort 3000
sudo bash /opt/av1-pipeline/install/activeer-lan-service.sh

# SMB-bibliotheken via de begrensde mount-helper
sudo bash /opt/av1-pipeline/install/activeer-bibliotheekbronnen.sh
```

Meer details, inclusief UFW, extra werkschijf en herstel na stroomuitval, staan
in `install/README.md`.

---

## Ontwikkelen en testen

Maak eerst korte testbestanden van zes seconden met alle relevante eigenschappen
(HDR10, Dolby Vision 8.1, ASS met lettertype, mov_text in MP4, lage bitrate,
al AV1, hardlinks). Test daarop, nooit op je echte films.

```bash
./test/maak-testbestanden.sh testdata
python3 startcode/probe.py testdata/hdr10.mkv | grep is_hdr10   # -> true
```

Backend:

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy
```

Frontend:

```bash
cd frontend
npm run dev        # ontwikkelserver
npm run lint
npm run typecheck
```

Handmatig via de commandoregel, vanuit `backend/` met de venv actief:

```bash
python -m app.cli probe   film.mkv        # stream- en frame-metadata
python -m app.cli beslis  film.mkv        # wat zou de pipeline doen, en waarom
python -m app.cli commando film.mkv       # het ffmpeg/mkvmerge-commando
python -m app.cli scan 1 --dry-run        # bibliotheek 1 scannen zonder opslaan
python -m app.cli besluiten 1             # openstaande besluiten
python -m app.cli goedkeuren 1 --actie encoderen
python -m app.cli wachtrij
python -m app.cli pauzeer gpu
```

---

## Webinterface

| Pagina | Wat je er doet |
|---|---|
| Dashboard | live fps, ETA, belasting, besparing, fouten |
| Wachtrij | gevirtualiseerde wachtrij, pauzeren per baan |
| Workers | CPU- en GPU-baan, temperatuur en vermogen |
| Beslissingen | goedkeuren, afwijzen, redenen inzien |
| Bibliotheken | lokale en SMB-bronnen toevoegen met een echte schrijftest |
| Profielen | encodeprofielen met proefencode en voor/na-vergelijking |
| Kwaliteit | VMAF-metingen en kwaliteitsadvies |
| AI | canary-planner en begrensde AI-controller |
| Fouten | gegroepeerde fouten met pogingshistorie |
| Logs | logregels, live via WebSocket |
| Instellingen | opslagkeuze, LAN-toegang, integraties, schakelaars, hardwarediagnose |

Integraties met Jellyfin, Radarr en Sonarr zijn optioneel. De API-sleutels
worden versleuteld in SQLite bewaard met de sleutel uit
`/etc/av1-pipeline-secrets.key`. Bewaar dat bestand bij een back-up.

---

## Stand van het project

Alle twaalf bouwfasen zijn afgerond, inclusief de incrementele scanscheduler en
de AI-controller. Nog open: schaduwevaluatie van de AI-controller, een
library-canary, een rebootcontrole en een duurtest van meerdere weken. Het
actuele projectgeheugen staat onderaan `AGENTS.md`.

## Stijl

- Nederlands in commentaar, docstrings, logregels en UI-tekst.
- Engels voor code: functienamen, variabelen, tabelnamen.
- Commentaar legt uit waarom, niet wat.
- Typehints overal in de backend, `strict` in TypeScript, geen bare `except:`.
