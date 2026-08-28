# Fase 4 — Wachtrij en twee banen

**Doel:** de CPU- en GPU-baan draaien tegelijk en werken de wachtrij af zonder
dat jij erbij hoeft te zijn.

**Duur:** 3-4 uur.

---

## Lees eerst

- `spec/01-architectuur.md` — planner en worker
- `spec/04-veiligheidsregels.md` regel 11

## Opdracht

**Planner** (`app/planner.py`), draait als achtergrondtaak in FastAPI:

- kijkt per baan of er een vrije worker is
- pakt de job met de hoogste prioriteit uit die baan
- claimt hem atomisch:
  `UPDATE job SET status='bezig', worker_id=? WHERE id=? AND status='wacht'`
  en controleert `rowcount` — zo kan geen twee workers dezelfde job pakken
- start de worker

**Worker-pool**: per baan een instelbaar aantal, standaard 1. De banen zijn
strikt gescheiden: een CPU-job komt nooit op de GPU-baan terecht, ook niet als
die leegstaat. Wil je dat toch, dan wijzig je het besluit — expliciet, via de
UI.

> Dit is de reden dat je dit bouwt. Je Arc doet duizenden SDR-afleveringen
> terwijl je CPU aan een handvol 4K HDR-films werkt. Verschillende hardware,
> geen wachtrij op elkaar.

**Bij het opstarten**: jobs die op `bezig` staan maar geen levend proces hebben
(de backendservice is herstart) terugzetten naar `wacht` en hun cache opruimen.

**Stiltevenster**: per baan instelbaar. Staat er een venster actief, dan start
de planner geen nieuwe jobs in die baan. Lopende jobs maakt hij af — een
4K-encode halverwege afbreken is zonde.

**Machinebescherming:** respecteer de algemene limiet voor CPU-gebruik en CPU-
temperatuur. Boven de ingestelde temperatuur start de CPU-baan geen nieuw werk
en krijgt de gebruiker een duidelijke melding. Onder de cache-pauzegrens
starten beide banen geen nieuw werk; de waarschuwings- en pauzegrens zijn via
Instellingen aanpasbaar. Bewaak ook een optionele Jellyfin-controle: zodra er
een actieve afspeelsessie is, pauzeert alleen de CPU-baan; de GPU-baan gaat
door tenzij de gebruiker hem zelf pauzeert.

**SSD-reservering:** reken niet alleen met vrije ruimte. Reserveer atomisch
`CACHE_RESERVE_FACTOR × bron_grootte` voor iedere actieve job in de database
en tel bestaande reserveringen mee. Met de eigen 1 TB-SSD is standaard één
CPU- en één GPU-job (`MAX_ACTIVE_JOBS=2`) toegestaan. Ontbreekt de SSD-mount,
is hij niet schrijfbaar of is de reserve niet beschikbaar, dan blijft de job
wachten met de reden in de UI.

**CLI en API:**

```bash
python -m app.cli planner-start
python -m app.cli wachtrij            # wat staat er, per baan
python -m app.cli pauzeer <baan>
```

## Acceptatietest

```bash
# Vul de wachtrij met de testbestanden, start de planner
python -m app.cli wachtrij
```

- beide banen draaien **tegelijk** — controleer met `ps` dat er twee
  ffmpeg-processen zijn
- een baan pauzeren stopt alleen die baan
- de backendservice herstarten midden in een job: bij het opstarten staat die job
  weer op `wacht` en de cache is leeg
- twee planners tegelijk starten mag nooit dezelfde job dubbel oppakken

## Niet doen

Geen Celery, geen Redis, geen RQ. `asyncio` plus een `status`-kolom is genoeg
voor twee workers. Elke extra dienst is iets dat om drie uur 's nachts stuk kan.
