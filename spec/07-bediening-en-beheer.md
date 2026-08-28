# Bediening en beheer

Dit document maakt de beheerschermen, waarschuwingen en integraties onderdeel
van het product. Het vult `05-ui.md` aan; bij tegenspraak gaan de
veiligheidsregels in `04-veiligheidsregels.md` voor.

## Navigatie en algemeen gedrag

De vaste navigatie is: Dashboard, Beslissingen, Wachtrij, Workers,
Bibliotheken, Profielen, Kwaliteit, Logs, Fouten en Instellingen.
Donkere modus is de standaard. Ondersteun `j`/`k` voor rijen, `/` voor zoeken
en `?` voor een zichtbaar overzicht van sneltoetsen. Iedere knop is met
toetsenbord bereikbaar, heeft een duidelijke naam voor schermlezers en toont
bij gevaarlijke acties een bevestiging.

Toon de servernaam en tijdzone in de kop. Tijdstempels worden in de ingestelde
tijdzone weergegeven. Een geselecteerd logniveau bepaalt wat naar het archief
gaat; fouten worden nooit door een te laag logniveau verborgen.

## Dashboard en workers

Naast de bestaande kaarten toont het dashboard: CPU-limiet, temperatuur,
cache-waarschuwing, QSV-status en de reden van een actieve pauze. Toon ook
wanneer de volgende batch volgens de 7-daagse prognose klaar is en welke baan
de bottleneck is. Browsermeldingen zijn opt-in voor fouten en afgeronde
batches.

De Workers-pagina is geen grafische versiering: per CPU- en GPU-baan zijn
worker-aantal, encoder, `nice`, stiltevenster, huidige job, laatste twintig
jobs, doorvoer, besparing en pauzereden zichtbaar en instelbaar. Een hitte-,
cache- of Jellyfin-pauze mag niet worden voorgesteld als een storing.

## Beslissingen, hardlinks en wachtrij

Beslissingen zijn altijd op besluit gegroepeerd en kunnen per groep of per
bestand worden aangepast. Corrigeer voorbeeldtellingen zorgvuldig (bijvoorbeeld
"1.199", nooit "1999"). Voor `opruimen` blijft een rode, tweede bevestiging
met aantal en omvang verplicht.

Een hardlink krijgt de badge **HARDLINK GEDETECTEERD**. Standaard is de actie
"overslaan". Optioneel kan de eigenaar het beleid wijzigen in **hardlink
verbreken na bevestiging**. De interface toont dan dat het torrentbestand oud
blijft en de besparing niet netto hoeft te zijn. Noem dit nooit herstellen van
een hardlink.

De wachtrij ondersteunt filters, prioriteit, CPU/GPU-wissel, bulk-pauzeren en
bulk-afbreken. "Afbreken" verwijdert alleen tijdelijke cache-output; de bron
blijft onaangeraakt. Gebruik daarom geen tekst die suggereert dat het
mediabestand wordt verwijderd.

## Bibliotheken en profielen

Een library-kaart toont pad, profiel, aantallen, omvang, scanmoment en
voortgang. "Genereer optimalisatievoorstellen" analyseert alleen en plant
niets automatisch in. Scan, droogloop en profielwisseling zijn afzonderlijke
acties.

Een profiel beschrijft alle beslisinstellingen, met korte uitleg: drempels,
CRF, preset, CPU/GPU-keuze, HDR-beleid, Dolby Vision-modus, talen,
ondertitel- en attachmentbeleid. HDR10+ is hier altijd alleen-lezen als
"overslaan"; er bestaat geen forceeroptie. De proefdraai maakt een fragment
uit het midden met meerdere CRF-waarden en toont encode-tijd, grootte en VMAF.

## Kwaliteit, logs en fouten

De Kwaliteit-pagina toont de verificatiebadge, bron/resultaat-frames met
vergelijkingsschuif en VMAF voor een beperkte steekproef. Als de aanwezige
FFmpeg geen `libvmaf` heeft, is de functie zichtbaar maar uitgeschakeld met
een concrete uitleg.

Logs bieden live tail, zoekarchief, niveau-filter en "kopieer exact commando".
Een regel die HDR10+ met succes zou hebben behouden, is ongeldig en moet als
overslaar-detectie worden gelogd.

Fouten worden gegroepeerd op oorzaak met bestand, exacte opdracht, laatste 50
logregels, diagnose, gevolgen en veilige actie. Voorbeelden:

- HDR-verificatie mislukt: bron blijft intact; alleen profiel aanpassen of
  veilig opnieuw proberen.
- QSV niet beschikbaar: diagnostiek en QSV-test; tijdelijk CPU kiezen is een
  expliciete keuze, geen automatische degradatie.
- Cache te laag: pas grens of ruimte aan; tijdelijk cachemateriaal mag alleen
  worden opgeruimd wanneer het niet bij een actieve job hoort.

Een "driver reset" reset nooit een kernel-driver vanuit de webapp. Hij start
hoogstens het optionele GPU-meetproces opnieuw en verwijst voor herstel van de
host naar de beheerder. De QSV-test is een korte niet-destructieve test die
de renderer, VAAPI en `av1_qsv` controleert.

## Externe koppelingen

Jellyfin pauzeert desgewenst de CPU-baan bij een echte afspeelsessie. Radarr en
Sonarr herlezen na een geslaagde vervanging uitsluitend het item dat op pad is
gevonden; zij starten nooit een bibliotheekbrede herindexering. API-sleutels
zijn geheimen: versleuteld bewaren, nooit in logs of in een browserantwoord.
