# Codex-pakket — je eigen AV1-pipeline

Alles wat Codex nodig heeft om deze pipeline voor je te bouwen: de
specificatie, twaalf gefaseerde prompts, werkende startcode en een testomgeving.

Deze editie is voor **Ubuntu Server 26.04 LTS, native op de host**. Er draait
geen Docker: de backend en frontend worden systemd-services. Zie `install/` en
`systemd/` voordat je met fase 0 begint.

**Wat je krijgt:** een pipeline die je bibliotheek scant, per bestand beslist
wat er moet gebeuren, jou dat laat goedkeuren, en het daarna op twee banen
tegelijk uitvoert — Intel Arc voor SDR 1080p, CPU voor 4K en HDR — met een
React-dashboard waarin je realtime fps, ETA, GPU- en CPU-belasting, bitrate,
besparing, fouten en logs ziet.

**Wat het kost:** 45 tot 60 uur, verspreid over avonden. Na fase 3 (ongeveer
tien uur) heb je iets dat werkelijk ruimte bespaart via de commandoregel.

---

## Doe dit eerst

```bash
# 1. Nieuwe repo
mkdir av1-pipeline && cd av1-pipeline && git init

# 2. Pakket erin
cp -R /pad/naar/codex-pakket/. .
# AGENTS.md staat in de projectwortel; Codex leest het automatisch bij elke taak.

# 3. Testbestanden maken (seconden)
./test/maak-testbestanden.sh testdata

# 4. Controleren dat de startcode werkt in jouw omgeving
python3 startcode/probe.py testdata/hdr10.mkv | grep is_hdr10
# -> "is_hdr10": true
```

Geeft die laatste `false`, dan mist je ffmpeg-build iets. Los dat eerst op:
alles daarna bouwt hierop.

---

## Wat er in zit

| Map | Wat het is |
|---|---|
| `AGENTS.md` | projectinstructies en projectgeheugen — staat in de wortel van je repo |
| `spec/01-architectuur.md` | hoe het in elkaar zit, en waarom zo |
| `spec/02-domeinkennis.md` | **de vijftien valkuilen — lees dit zelf ook** |
| `spec/03-datamodel.md` | het SQLite-schema |
| `spec/04-veiligheidsregels.md` | de regels die niet onderhandelbaar zijn |
| `spec/05-ui.md` | alle schermen, plus aanbevolen toevoegingen |
| `spec/06-dolby-vision.md` | wat er met DV gebeurt, en wat er onzeker aan is |
| `spec/07-bediening-en-beheer.md` | instellingen, kwaliteit, herstel en veilige koppelingen |
| `spec/08-opslag-herstel-en-schakelaars.md` | systeemschijf/werkruimte, crashherstel en alle aan/uit-opties |
| `spec/09-figma-afstemming.md` | koppeling met het Figma-ontwerp en verplichte verschillen |
| `prompts/00` t/m `11` | de bouwfasen, op volgorde |
| `startcode/` | vijf modules die al werken en getest zijn |
| `test/` | testbestanden genereren en de tests zelf |

---

## Hoe je de prompts gebruikt

Eén fase per Codex-taak. Start de taak in de wortel van de repo en geef deze
opdracht:

```
Lees AGENTS.md en de relevante bestanden in spec/. Voer daarna
prompts/00-scaffold.md uit.
```

Codex leest `AGENTS.md` ook automatisch voordat het aan het werk gaat. De
expliciete verwijzing hierboven maakt duidelijk welke fase je wilt uitvoeren.

Elke prompt bevat een **acceptatietest**. Ga niet door naar de volgende fase
voordat die groen is. Dat klinkt streng, maar de reden is praktisch: als fase 3
je bestanden niet veilig vervangt, bouw je in fase 4 tot en met 9 bovenop iets
dat je later moet uitgraven.

Werk aan het eind van elke taak het onderste blok van `AGENTS.md` bij, zodat
de volgende sessie weet waar je bent.

Voor frontendwerk gebruik je ook het [Figma-ontwerp](https://www.figma.com/design/9CQugincBWbZfyDj7xOtiB/Untitled?node-id=0-1&t=FT6CCCZHlwyxKpiI-1),
maar lees vooraf `spec/09-figma-afstemming.md`: het pakket bevat verplichte
veiligheidscorrecties die in het huidige ontwerp nog niet overal staan.

| Fase | Onderwerp | Uur | Wat je daarna hebt |
|---|---|---|---|
| 0 | Scaffold | 1-2 | repo, database, healthcheck |
| 1 | Analyse | 2-3 | `beslis film.mkv` zegt wat er moet gebeuren |
| 2 | Catalogus | 3-4 | hele bibliotheek gescand en beoordeeld |
| 3 | Uitvoeren | 4-5 | **één bestand veilig verkleind** |
| 4 | Wachtrij en workers | 3-4 | twee banen die tegelijk doordraaien |
| 5 | API en WebSocket | 3-4 | live gegevens beschikbaar |
| 6 | Frontend basis | 5-7 | dashboard, wachtrij, workers |
| 7 | Frontend rest | 5-7 | beslissingen, library, profielen, logs, fouten |
| 8 | Extra's | 3-5 | VMAF, proefdraai en *arr |
| 9 | Hardening | 3-4 | weken achter elkaar draaien |
| 10 | Beheer en integraties | 4-6 | alle beheerfuncties, veilig afgerond |
| 11 | Opslag, herstel en schakelaars | 5-8 | single-diskveilig, uitbreidbaar en crashbestendig |

---

## Drie dingen die je tijd schelen

**Test op de korte bestanden, niet op je films.** `test/maak-testbestanden.sh`
maakt bestanden van zes seconden met alle eigenschappen die ertoe doen: HDR10
met mastering display, ASS met ingesloten lettertype, mov_text in een MP4, lage
bitrate, al AV1. Een testronde is dan twintig seconden in plaats van een avond.

Dit is de belangrijkste tip in dit hele document. Je feedbackloop is een
encode, en dat is wat de schatting van twintig uur normaal opblaast.

**Herschrijf de startcode niet.** Ziet iets er omslachtig uit, kijk dan eerst
in `spec/02-domeinkennis.md`. Vrijwel elke rare regel dekt een valkuil af die
stil faalt.

**Bouw het beslissingenscherm vroeg.** Het staat in fase 7, maar als je merkt
dat je de wachtrij niet vertrouwt, haal het dan naar voren. Zien wat er gaat
gebeuren vóórdat het gebeurt is het hele verschil met een kant-en-klare tool.

---

## Terwijl je bouwt

Laat Unmanic doorwerken op je anime-map. Die bestanden zijn dan al AV1 als je
eigen pipeline er ooit langskomt, en die slaat ze gewoon over. Je verliest
niets door beide te draaien — zolang ze niet dezelfde map tegelijk aanraken.
Zie `prompts/09-hardening.md`, het laatste onderdeel.
