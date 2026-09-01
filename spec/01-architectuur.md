# Architectuur

## In één plaatje

```
┌──────────────────────────────────────────────────────────────┐
│  Next.js + LAN-gateway    poort 3000                         │
│  Basic Auth op LAN · dashboard · workers · library · logs    │
└───────────────┬──────────────────────────┬───────────────────┘
                │ REST (mutaties)          │ WebSocket (live)
┌───────────────▼──────────────────────────▼───────────────────┐
│  FastAPI                  127.0.0.1:8000                    │
│                                                              │
│ scheduler ─> scanner ─> beslisser ─> AI-policy ─> wachtrij   │
│                 handmatige/API-scan ────────┘      │         │
│                                                    ▼         │
│                                                 planner      │
│                                             │                │
│                              ┌──────────────┴─────────┐      │
│                              ▼                        ▼      │
│                        worker "cpu"              worker "gpu" │
│                        SVT-AV1 (+ Dolby Vision)  av1_qsv      │
│                              │                        │      │
│                              └──────────┬─────────────┘      │
│                                         ▼                    │
│                                   verificatie                │
│                                   (HDR/DV terug?)            │
│                                         ▼                    │
│                                   vervanging                 │
└──────────────────────────┬───────────────────────────────────┘
                           │
                    SQLite (WAL)
```

Twee native systemd-services op één Ubuntu-host. De backend is de enige die
bij je media komt en is nooit rechtstreeks vanaf het LAN bereikbaar. De
frontend-gateway geeft HTTP en WebSocket pas vrij voor privé-adressen nadat
LAN-toegang met een apart wachtwoord is ingeschakeld.

Lokale bibliotheken mogen op ieder veilig toegankelijk mediapad staan.
SMB-URI's gaan via één root-owned, wachtwoordloos aanroepbare maar inhoudelijk
strikt begrensde mounthelper naar `/library/netwerk`; de backend kan daarmee
geen willekeurig rootcommando uitvoeren. Voor opslag van de library volgt
altijd een eerste-gebruiktest en voor iedere scan/job een nieuwe mountcheck.

---

## Waarom deze splitsing

**De backend moet een langlevend proces zijn.** Hij houdt worker-subprocessen
vast, leest hun voortgang uit, en moet een encode van vier uur kunnen
overleven. Next.js is daar het verkeerde gereedschap voor: serverless-achtige
route handlers en een lange ffmpeg-subprocess gaan niet samen.

**De frontend is puur weergave.** Alles wat je in de UI ziet komt uit de
backend. Er zit geen state in React die niet ook in SQLite staat. Herstart je
de frontend midden in een encode, dan mis je niets.

---

## Onderdelen

### scanner
Loopt door een library, vergelijkt eerst pad, grootte, mtime, hardlinks, device
en inode, en probet alleen nieuwe of gewijzigde media. Een bestand moet zestig
minuten oud en tijdens de probe byte-/inode-stabiel zijn. Verdwenen media wordt
als tombstone bewaard. De interne `asyncio`-scheduler rekent lokale 03:00-
momenten uit; scans overlappen nooit en gemiste momenten worden niet ingehaald.

### beslisser
Neemt een probe-resultaat plus een profiel en levert een `Besluit`
(`startcode/beslissen.py`). **Doet niets.** Het besluit wordt opgeslagen en
wacht op handmatige goedkeuring of de begrensde AI-policy — zie
`04-veiligheidsregels.md`, regel 1.

### AI-controller
Analyseert één gegroepeerde handtekening met een getypeerd voorstel. De
provider krijgt geen tools, ruwe paden, SQLite of ongefilterde logs. Alleen de
lokale Python-policy kan daarna een waarschuwingvrij besluit bevestigen. Een
providerfout, budgetstop of verschil voert niets uit. De providerinterface is
losgekoppeld zodat een lokaal model later eerst in schaduwmodus kan meelopen.

### wachtrij + planner
Goedgekeurde besluiten worden jobs. De planner kent elke job toe aan een baan
(`cpu` of `gpu`) op basis van het besluit, en geeft de volgende job aan een
vrije worker in die baan.

Dit is de kern van waarom je dit bouwt: **twee banen die echt tegelijk
draaien.** Je Arc doet duizenden SDR-afleveringen terwijl je CPU aan een
handvol 4K HDR-films werkt. Verschillende hardware, geen wachtrij op elkaar.

Standaard één worker per baan. Op een host met minder dan 24 GiB begrenst de
planner de effectieve media-concurrency tot één job totaal; OOM-kills krijgen
nooit een AI-retry.

### worker
Draait één job:

1. bouwt het commando (`startcode/commandos.py`)
2. start het als subprocess met `-progress pipe:1`
3. leest voortgang mee en publiceert die (fps, ETA, bitrate, grootte)
4. schrijft naar de **cachemap**, nooit rechtstreeks over het origineel
5. verifieert het resultaat
6. laat pas dan vervangen

### verificatie
Vergelijkt de metadata van het origineel — vastgelegd vóór de encode — met het
resultaat. Is de HDR of DV weg, dan is de job **mislukt** en wordt het origineel
niet aangeraakt.

> Dit is het onderdeel dat in de Unmanic-plugin stuk was: die vergeleek na
> afloop het nieuwe bestand met zichzelf, waardoor de controle nooit kon
> afgaan. Leg de bron-metadata vast vóórdat je begint.

### vervanging
Crashveilig en atomisch op de bibliotheekschijf. Het
geverifieerde resultaat gaat eerst als verborgen tijdelijk bestand naar de map
van het origineel. Pas na `fsync`, behoud van eigenaar/rechten/ACL/xattrs en
een vastgelegde herstelstatus wordt de bestandsnaam atomisch gewisseld. Zie
`04-veiligheidsregels.md` en `08-opslag-herstel-en-schakelaars.md`.

---

## Techniek

| Laag | Keuze | Waarom |
|---|---|---|
| Platform | Ubuntu Server 26.04 LTS | actuele kernel, Intel-media-stack en FFmpeg 8; Ubuntu 24.04 zit op 6.1 en is te oud voor de DV-tak |
| Backend | Python 3 + FastAPI in een virtual environment | ffmpeg-orkestratie is subprocess-werk; de startcode is al Python |
| Database | SQLite (WAL) | één schrijver, geen server, prima voor 500k rijen |
| Workers | `asyncio.create_subprocess_exec` | geen Celery, geen Redis; je hebt twee workers, geen cluster |
| Live data | WebSocket | één kanaal, server pusht |
| Frontend | Next.js (App Router) + React | zijn keuze; goede fit voor een dashboard met veel live panelen |
| Grafieken | Recharts of uPlot | uPlot als je duizenden punten wilt tonen |
| Styling | Tailwind + shadcn/ui | snel tot iets dat er af is, zonder eigen componentbibliotheek |
| Draaien | systemd | start bij boot; de servicegebruiker krijgt de groepen `render` en `video` |

**Geen Celery, geen Redis, geen Postgres.** Je hebt één machine, twee workers
en een paar honderdduizend bestanden. Elke extra dienst is iets dat om drie uur
's nachts stuk kan.

---

## Live gegevens

Eén WebSocket op `/ws`. De server pusht elke seconde een bericht:

```jsonc
{
  "type": "tick",
  "workers": [
    { "baan": "gpu", "job_id": 4211, "bestand": "Frieren - S01E04.mkv",
      "percent": 62.5, "fps": 148.2, "speed": 6.2,
      "eta_seconden": 94, "bitrate_kbps": 1820,
      "grootte_nu": 412000000, "grootte_bron": 2100000000 }
  ],
  "systeem": {
    "cpu_percent": 94.1, "cpu_temp": 71,
    "gpu": { "video_percent": 88.0, "render_percent": 12.0,
             "freq_mhz": 2100, "power_w": 34.2, "beschikbaar": true },
    "schijf_vrij_gb": 412
  },
  "wachtrij": { "wachtend": 8214, "bezig": 2, "klaar_vandaag": 143, "fouten": 2 }
}
```

### Waar die getallen vandaan komen

**fps, bitrate, grootte, ETA** — allemaal uit één bron: `-progress pipe:1` bij
ffmpeg. Dat schrijft `key=value`-regels naar stdout:

```
frame=1234
fps=148.20
bitrate=1820.3kbits/s
total_size=412000000
out_time_ms=62500000
speed=6.2x
progress=continue
```

ETA reken je uit als `(duur - out_time) / speed`. Alle encoders in dit
project zijn ffmpeg, dus dit werkt overal hetzelfde — ook voor de Dolby
Vision-tak, sinds die niet meer via HandBrake loopt.

**CPU** — `psutil.cpu_percent()` plus `psutil.sensors_temperatures()`.

**GPU (Intel Arc)** — `intel_gpu_top -J -s 1000` levert een JSON-stroom met
per-engine bezetting. De engine die telt bij AV1-encoden heet **Video**.

> `intel_gpu_top` kan native toegang tot `/sys/kernel/debug` of extra
> perf-rechten vragen. Dat is meer toegang dan de mediatool normaal krijgt.
>
> **Maak GPU-monitoring optioneel.** Draait `intel_gpu_top` niet, dan zet je
> `gpu.beschikbaar = false` en toont de UI "GPU-meting niet beschikbaar" — en
> werkt de rest gewoon door. Encoden heeft die rechten niet nodig, alleen
> lees- en schrijfrechten op `/dev/dri/renderD128` via de groepen `render` en
> `video`.

---

## Mappen

```
/library/...                         je media, aangekoppeld zoals in Unmanic
/srv/av1-pipeline/cache              systeemschijf: standaard encode-cache
/srv/av1-pipeline/work               systeemschijf: standaard tijdelijk werk
/srv/av1-pipeline/config             systeemschijf: vaste SQLite, logs en opslagkeuze
/srv/av1-pipeline/backups            systeemschijf: standaard lokale herstelkopieën
/mnt/av1-werkruimte/{cache,work}     optioneel later gemounte werkruimte via de UI
```

De cache staat **buiten** `/library`. Een half afgemaakte encode mag nooit door
Jellyfin gezien worden. Een geverifieerd resultaat vervangt het origineel met
één atomische naamwissel op de bibliotheekschijf; het origineel wordt daarbij
permanent verwijderd. Een lokale back-up op dezelfde systeemschijf is nuttig
voor snel databaseherstel, maar beschermt niet tegen schijfdefect; kopieer
back-ups naar een tweede locatie.
