# Fase 5 — API en WebSocket

**Doel:** alles wat de frontend nodig heeft is beschikbaar, en de live gegevens
stromen. Nog geen React.

**Duur:** 3-4 uur.

---

## Lees eerst

- `spec/01-architectuur.md` — het onderdeel "Live gegevens"
- `spec/05-ui.md` — om te weten welke gegevens de schermen nodig hebben

## Opdracht

### REST

```
GET    /api/health
GET    /api/libraries                 lijst met voortgang per library
POST   /api/libraries                 toevoegen
PATCH  /api/libraries/{id}            profiel wisselen, scan-interval
POST   /api/libraries/{id}/scan       scan starten (achtergrond)
POST   /api/libraries/{id}/droogloop  alleen besluiten genereren
GET    /api/libraries/{id}/suggesties veilige profiel-/scanvoorstellen

GET    /api/profielen
POST   /api/profielen
PATCH  /api/profielen/{id}
POST   /api/profielen/{id}/proefdraai

GET    /api/besluiten                 ?status=&actie=&library_id=  gegroepeerd
POST   /api/besluiten/goedkeuren      {ids:[]} of {filter:{...}}
POST   /api/besluiten/afwijzen
PATCH  /api/besluiten/{id}            handmatig overrulen (baan, encoder)

GET    /api/jobs                      ?status=&baan=  met paginering
POST   /api/jobs/{id}/afbreken
POST   /api/jobs/{id}/opnieuw
PATCH  /api/jobs/{id}                 prioriteit, baan wisselen
POST   /api/jobs/{id}/vmaf

GET    /api/workers                   toestand per baan
POST   /api/workers/{baan}/pauzeer
PATCH  /api/workers/{baan}            aantal, nice, stiltevenster

GET    /api/jobs/{id}/log             ?vanaf=  voor de tail
GET    /api/fouten                    gegroepeerd op foutsoort
GET    /api/statistieken              besparing per dag, doorvoer, prognose
GET    /api/instellingen
PATCH  /api/instellingen              naam, tijdzone, logniveau, limieten,
                                      meldingen en hardlinkbeleid
GET    /api/functies                  alle optionele schakelaars met uitleg
PATCH  /api/functies/{sleutel}        {ingeschakeld, configuratie}
POST   /api/systeem/qsv-test          onschadelijke VAAPI/QSV-capabiliteitstest
POST   /api/systeem/gpu-herstart      alleen diagnostiek/herstart meetproces;
                                      nooit blind kernel-drivers resetten
GET    /api/systeem/diagnose
GET    /api/systeem/mounts            werkopslag- en librarystatus
POST   /api/backups/nu
POST   /api/backups/{id}/controleer-herstel

POST   /api/integraties/jellyfin/test
POST   /api/integraties/radarr/test
POST   /api/integraties/sonarr/test
```

Alles wat lang duurt (scannen) start een achtergrondtaak en geeft meteen een
id terug. Nooit een request van vijf minuten open laten staan.

### WebSocket

Eén endpoint `/ws`, de server pusht elke seconde het `tick`-bericht uit
`spec/01-architectuur.md`. Daarnaast losse berichten bij gebeurtenissen:

```jsonc
{"type":"job_klaar",   "job_id":4211, "besparing_bytes":1688000000}
{"type":"job_mislukt", "job_id":4212, "reden":"HDR10-metadata verdwenen"}
{"type":"scan_klaar",  "library_id":1, "nieuw":143, "gewijzigd":2}
{"type":"log", "job_id":4211, "niveau":"info", "tekst":"..."}
{"type":"melding", "soort":"fout", "titel":"QSV niet beschikbaar", "actie":"qsv-test"}
```

Logregels alleen sturen naar clients die zich op die job hebben geabonneerd
(`{"actie":"volg","job_id":4211}`), anders stuur je bij twee actieve encodes
duizenden regels per seconde naar een dashboard dat ze niet toont.

### Systeemmetingen (`app/systeem.py`)

- **CPU**: `psutil.cpu_percent(interval=None)` en
  `psutil.sensors_temperatures()`
- **Schijf**: vrije en gereserveerde ruimte in `CACHE_ROOT`, want daar loop je
  als eerste vast
- **GPU**: `intel_gpu_top -J -s 1000` als langlopend subprocess, JSON meelezen.
  De engine die telt bij AV1-encoden heet **Video**.

> `intel_gpu_top` kan native toegang tot `/sys/kernel/debug` of aanvullende
> perf-rechten vragen. Dat is fors.
>
> **Maak het optioneel.** Start het niet of faalt het, zet dan
> `gpu.beschikbaar = false` en ga door. Encoden zelf heeft die rechten niet
> nodig — alleen `/dev/dri`. Een dashboard dat een meter mist is beter dan een
> backendservice die niet start.

## Acceptatietest

```bash
curl -s localhost:8000/api/statistieken | jq
websocat ws://localhost:8000/ws | head -5
```

- het `tick`-bericht komt elke seconde en bevat `workers`, `systeem`, `wachtrij`
- draait er een encode, dan lopen `percent` en `fps` op
- `intel-gpu-tools` weghalen van de host mag de rest niet breken:
  `gpu.beschikbaar` wordt `false`, alles blijft werken
- OpenAPI staat op `/docs` en klopt
- `POST /api/systeem/qsv-test` draait een korte, tijdelijke encode of
  capability-check en laat geen mediabestand achter
- een GPU-monitor die faalt maakt de QSV-test of encoder niet onbruikbaar

## Niet doen

Geen polling-endpoints voor live data. Eén WebSocket, en de server bepaalt het
tempo.
