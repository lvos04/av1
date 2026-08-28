# Fase 10 — Beheer, kwaliteitscontrole en integraties

**Doel:** maak van de werkende pipeline een dashboard dat je veilig wekenlang
kunt gebruiken, zonder losse terminalstappen of misleidende knoppen.

**Duur:** 4-6 uur.

---

## Lees eerst

- `spec/07-bediening-en-beheer.md` — volledig
- `spec/04-veiligheidsregels.md` — regels 13 en 14 zijn harde grenzen
- `spec/03-datamodel.md`
- `spec/08-opslag-herstel-en-schakelaars.md` — schakelbare onderdelen
- `spec/09-figma-afstemming.md`
- `prompts/08-extras.md` en `prompts/09-hardening.md`

## Opdracht

Werk de bestaande backend en frontend af met deze onderdelen.

### Instellingen

Bouw `/instellingen` met servernaam, tijdzone, logniveau, CPU-limiet,
temperatuurgrens, cache-waarschuwing, cache-pauze, hardlinkbeleid,
browsermeldingen. Valideer elke invoer server-side.

Sla paden en niet-geheime startwaarden op volgens de env-configuratie; sla
bedieningswaarden op in SQLite. Geheime sleutels voor Jellyfin, Radarr en
Sonarr worden versleuteld opgeslagen en komen nooit terug in een API-response.
Toon slechts of een sleutel is ingesteld.

### Hardware en fouten

Maak een duidelijke systeemdiagnose met render node, VAAPI, oneVPL,
`av1_qsv`, temperatuur, cache en GPU-monitorstatus. Voeg een niet-destructieve
knop **Test QSV-verbinding** toe. Deze mag alleen een capability-check of zeer
korte tijdelijke test uitvoeren, moet tijdelijke data verwijderen en geeft
bruikbare diagnose terug.

Een knop **Herstart GPU-monitor** herstart uitsluitend het optionele
`intel_gpu_top`-meetproces. Noem dit geen driverreset en voer vanuit de
webapp nooit een kernel-driverreset uit.

Breid `/fouten` uit met oorzaak, impact, laatste 50 regels, exact commando,
veilige vervolgstap en opnieuw proberen. Er bestaat nergens een actie
"Negeer & forceer" voor HDR, HDR10+ of verificatieproblemen. Een mislukte
verificatie houdt de bron intact.

### Kwaliteit

Maak `/kwaliteit` met verificatiebadges, bron/resultaat-frames op hetzelfde
moment, een vergelijkingsschuif, VMAF-steekproef en proefdraaien met meerdere
CRF-waarden. Detecteer `libvmaf`; ontbreekt dit, dan leg je helder uit waarom
de functie niet beschikbaar is.

### Beslissingen, wachtrij en toegankelijkheid

Gebruik overal de vaste navigatie uit `spec/07-bediening-en-beheer.md`.
Beslissingen blijven gegroepeerd, `opruimen` blijft rood en een hardlink krijgt
de veilige standaardactie `overslaan`. Alleen wanneer de eigenaar het beleid
expliciet heeft gezet op `verbreek_na_bevestiging` én per bestand bevestigt,
mag die job worden ingepland. De bevestiging noemt de ruimte-impact en het feit
dat de torrent-hardlink niet wordt hersteld.

Voeg zoek- en toetsenbordbediening toe (`j`, `k`, `/`, `?`), donkere modus als
standaard en toegankelijke labels/focus voor alle acties. Pas teksten aan:
afbreken ruimt uitsluitend tijdelijke cache op en verwijdert nooit media.

### Jellyfin, Radarr en Sonarr

Maak opt-in configuratie, verbindingstest en duidelijke status voor alle drie.
Jellyfin pauzeert desgewenst alleen de CPU-baan als er echt wordt afgespeeld.
Radarr/Sonarr zoeken na een geslaagde vervanging eerst het specifieke item op
pad op en starten alleen daarvan een refresh. Een mislukte externe oproep wordt
gelogd als integratiewaarschuwing; de lokale, al-geverifieerde job blijft
geslaagd.

## Acceptatietest

- een HDR10+-bron krijgt uitsluitend `overslaan`; er is geen forceerroute in
  API of interface
- een hardlink wordt niet ingepland met standaardinstellingen; na de expliciete
  dubbele bevestiging toont de job de verwachte ruimte-impact
- een QSV-test laat geen bestanden in cache of bibliotheek achter
- een browser die meldingen weigert breekt het dashboard niet
- CPU-werk pauzeert bij een gesimuleerde Jellyfin-afspeelsessie, GPU-werk niet
- een Radarr/Sonarr-fout verandert een lokaal geslaagde job niet in `mislukt`
- de hele interface is zonder muis te bedienen

## Niet doen

- Geen kernel-driverreset via de webapp.
- Geen API-sleutels in logs, frontend-state, URL's of foutmeldingen.
- Geen automatische uitvoering van optimalisatievoorstellen.
- Geen permanente verwijdering als onderdeel van afbreken of als achtergrondtaak.
