# Projectinstructies en projectgeheugen

Dit bestand staat in de wortel van de repo. Codex leest `AGENTS.md`
automatisch voordat het aan een taak werkt, zodat je niet elke keer opnieuw
hoeft uit te leggen wat dit is.

---

## Wat dit is

Een pipeline die een mediabibliotheek van ~50 TB kleiner maakt: overbodige
audiosporen eruit, video naar AV1, zonder HDR of Dolby Vision te slopen.
Draait op één machine met een Intel Arc (GPU-baan) en een CPU (CPU-baan) die
tegelijk werken.

Persoonlijk project, één gebruiker, eigen netwerk. Geen multi-tenant, geen
schaalbaarheid, geen microservices.

## Stack

- **Backend**: Python 3 in een virtual environment, FastAPI, SQLite (WAL),
  `asyncio` voor de workers
- **Frontend**: Next.js (App Router), TypeScript, Tailwind, shadcn/ui, Recharts
- **Media**: ffmpeg **7.1+** (SVT-AV1, av1_qsv), mkvmerge, dovi_tool
- **Platform**: Ubuntu Server 26.04 LTS, native — geen Docker of containers
- **Draaien**: twee systemd-services; de backendgebruiker zit in de groepen
  `render` en `video` voor `/dev/dri/renderD128`

Geen Celery, geen Redis, geen Postgres, geen ORM. Twee workers en een
`status`-kolom.

## Lees dit voordat je aan media-code werkt

`spec/02-domeinkennis.md` bevat vijftien valkuilen die **allemaal stil falen**.
HDR10-metadata zit in frame-side-data en niet in stream-side-data; `av1_qsv`
kan mastering display helemaal niet meedragen; mkvmerge wil een andere
kleurvolgorde dan SVT-AV1; Dolby Vision profiel 5 mag je nooit aanraken.

Je merkt het niet in de logs. Je merkt het op je TV, na vijfhonderd bestanden.

## Dolby Vision

Standaard staat `dv_modus` op `skip`. De tak is gebouwd en de metadata blijft
behouden, maar AV1 met DV profiel 10 speelt nergens gedocumenteerd af — niet in
Jellyfin, niet op een Apple TV. Zie `spec/06-dolby-vision.md` voordat je hier
iets aan verandert.

Vereist **ffmpeg 7.1+**: vanaf die versie schrijft `libsvtav1` de RPU zelf weg
met `-dolbyvision 1`. Die 1 moet **expliciet**; de standaardstand `auto` gooit
je Dolby Vision stilzwijgend weg. HandBrake is hier niet meer voor nodig.

Profiel 7 (dual layer) heeft nog wel `dovi_tool` nodig om eerst naar 8.1 te
gaan. Ontbreekt dat, dan slaat de beslisboom die bestanden over met een reden —
nooit half uitvoeren.

## Regels die niet onderhandelbaar zijn

Staan voluit in `spec/04-veiligheidsregels.md`. De kern:

1. **Besluiten en uitvoeren zijn gescheiden.** De scanner beslist, de gebruiker
   keurt goed, pas dan draait er iets.
2. **Het origineel wordt nooit overschreven vóór verificatie.** Encoderen naar
   de cache, controleren, dan pas vervangen.
3. **Verifiëren doe je met metadata van vóór de encode.** Vastgelegd op de job.
   Niet het bronpad opnieuw probén nadat je hebt vervangen — dan vergelijk je
   het nieuwe bestand met zichzelf.
4. **Permanent vervangen gebeurt alleen atomisch ná verificatie.** Faalt een
   controle, dan blijft de bron onaangeraakt.
5. **Bij twijfel overslaan.** Een overgeslagen bestand kost niets; een
   verkeerd verwerkt bestand kost een re-download van 60 GB.
6. **Nooit `shell=True`.** Altijd een lijst met argumenten.
7. **HDR10+ altijd overslaan.** Geen forceerknop, geen stille uitzondering.
8. **Hardlinks standaard overslaan.** Een expliciete keuze mag alleen de
   hardlink verbreken; de UI mag nooit herstel beloven.
9. **De 1 TB-SSD is cache en werkruimte.** De geverifieerde vervanging gebeurt
   op de bibliotheekschijf zelf.
10. **Bestandsrechten en hersteltransacties zijn verplicht.** Deze onderdelen
   zijn niet via de UI uitschakelbaar.

Lees voor beheerfuncties, kwaliteitscontrole, instellingen en externe
koppelingen ook `spec/07-bediening-en-beheer.md`. API-sleutels zijn geheimen:
versleuteld opslaan en nooit naar logs, de browser of een foutmelding sturen.
Lees voor de SSD-indeling, herstel na een crash en optionele schakelaars ook
`spec/08-opslag-herstel-en-schakelaars.md`.
Voor alle frontend- en Figma-aanpassingen lees je ook
`spec/09-figma-afstemming.md`. Het Figma-ontwerp is visueel uitgangspunt; de
veiligheidsregels van dit pakket gaan bij ieder verschil voor.

## Code die je niet moet herschrijven

`backend/app/media/` komt uit `startcode/` en is getest tegen echte bestanden:
een HDR10-bron met mastering display, anime met ASS en een ingesloten
lettertype, een MP4 met mov_text, en twaalf realistische bestandsnamen.

Elke regel die er raar uitziet, dekt een valkuil af die in
`spec/02-domeinkennis.md` staat uitgelegd. Vereenvoudig hem niet zonder daar
eerst te kijken.

## Testen

```bash
./test/maak-testbestanden.sh testdata     # eenmalig, seconden
python -m pytest backend/tests/ -v
```

**Je feedbackloop is een encode.** Test op de bestanden van zes seconden uit
`testdata/`, niet op echte films — anders kost elke bugronde uren en ben je na
drie fixes een avond kwijt.

**"Het commando ziet er goed uit" is geen test.** De enige test die telt is:
draai het, en lees de metadata van het resultaat terug. Alle vijftien valkuilen
falen stil.

## Stijl

- Nederlands in commentaar, docstrings, logregels en UI-tekst
- Engels voor code: functienamen, variabelen, tabelnamen
- Commentaar legt uit **waarom**, niet wat. `# tel op bij i` is ruis;
  `# p010le, niet yuv420p10le: QSV wil zijn eigen 10-bits formaat` is de reden
  dat het commentaar er staat
- Typehints overal in de backend, `strict` in TypeScript
- Geen bare `except:` — vang op wat je verwacht, en log de rest

## Mappen op de host

```
/library                         je media, read-write
/srv/av1-pipeline/cache          1 TB-SSD: werkmap, 2-3x grootste bestand
/srv/av1-pipeline/config         1 TB-SSD: SQLite en logs
```

De cache staat buiten `/library`: een half afgemaakte encode mag Jellyfin
nooit zien.

## Waar je bent

Werk de fasen af uit `prompts/`, op volgorde. Elke fase heeft een
acceptatietest; ga niet door voordat die groen is.

<!-- Houd hieronder bij waar je bent gebleven. -->

**Huidige fase:** voltooid — wacht op fysieke SSD/library, rebootcontrole en menselijke duur-/tv-test
**Klaar:** 0 — scaffold/host; 1 — analyse/DV; 2 — catalogus; 3 — veilige uitvoering, metadata/ACL/xattr, verificatie en atomische vervanging; 4 — wachtrij en workers; 5 — REST-API, WebSocket en systeembewaking; 6 — Next.js-frontend, live dashboard, gevirtualiseerde wachtrij en workers; 7 — beslissingen, bibliotheken, profielen, logs en gegroepeerde fouten; 8 — verificatiebadge, echte profielproef, voor/na, VMAF-detectie en versleutelde Jellyfin/Radarr/Sonarr-koppelingen; 9 — onderhoud, cachegrenzen, logrotatie en 100.000-job schaaltest; 10 — instellingen, hardwarediagnose, integraties, hardlinkbevestiging en toegankelijk beheer; 11 — SSD-controles, crashherstel, schakelaars, back-upmanifesten en canary-planner
