# Bediening en beheer

Dit document maakt de beheerschermen, waarschuwingen en integraties onderdeel
van het product. Het vult `05-ui.md` aan; bij tegenspraak gaan de
veiligheidsregels in `04-veiligheidsregels.md` voor.

## Navigatie en algemeen gedrag

De vaste navigatie is: Dashboard, Beslissingen, AI, Wachtrij, Workers,
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
De totale encodeconcurrency is een afzonderlijk positief geheel getal in
SQLite. Eén is de aanbevolen beginwaarde op de huidige 15-GiB-host, maar er is
geen vaste bovengrens die een latere RAM-upgrade blokkeert. De per-baanwaarden
bepalen hoeveel CPU- en GPU-workers daarvan gebruik mogen maken.

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

Een library-kaart toont pad, profiel, aantallen, omvang, de geverifieerde totale
besparing met het percentage van de verwerkte brondata, laatste en volgende
scan, stabiliteitswachtrij en voortgang. Scanpresets zijn Handmatig, Dagelijks
en Wekelijks; een interne scheduler rekent 03:00 in de ingestelde IANA-tijdzone
uit en haalt een tijdens uitschakeling gemist moment niet in. Scan, droogloop,
scanschema en profielwisseling zijn afzonderlijke acties.

Media- en duurprobes draaien nooit binnen een SQLite-schrijftransactie. Een
tijdelijke database-lock mag de planner, scanscheduler of AI-controller niet
definitief stoppen; iedere permanente achtergrondloop logt de fout en probeert
bij zijn volgende interval opnieuw.

Als een veiligheidswijziging de bestaande AI-toestemming intrekt, wordt een
eventuele half afgemaakte scan met behoud van historie afgesloten en de cursor
gewist. De daaropvolgende scan begint bewust bij het begin, zodat geen bestand
vóór de oude cursor met een verouderde toestemming achterblijft.

Een bibliotheek kan na een exacte tekstbevestiging uit de pipeline worden
verwijderd. Dit ruimt haar catalogus, besluiten, jobs, scans, AI-historie en
exact bekende lokale pipeline-output op. De bronmap, mediabestanden en een
eventuele SMB-mount worden nooit verwijderd of ontkoppeld. Een actieve scan,
proefdraai, job of onafgeronde vervanging blokkeert de actie.

Instellingen bevat een eigen AI-controllerkaart met provider, model,
schaduw/actief, bevoegdheden, maandbudget, verbruik, versleutelde sleutel en
de afzonderlijke HDR10-metadata-poort. Het model krijgt alleen gegroepeerde,
allowlisted technische velden en HMAC-paden. Actieve modus en HDR10-vrijgave
hebben ieder een exacte bevestiging. Een profiel-, media-, FFmpeg-hoofdversie-
of policywijziging trekt niet-gestarte AI-toestemming in en activeert canary.

AI heeft een eigen overzichtspagina met de getypeerde voorstellen, de lokale
policy-uitkomst, eventuele uitvoering, redencodes en bewijsvelden. Twee grafieken
tonen per lokale kalenderdag het token-/kostenverbruik en de aantallen besluiten
per classificatie. De pagina toont nooit de API-sleutel of ruwe bronpaden die
naar de provider zijn gestuurd.

De OpenAI-provider gebruikt `gpt-6-luna` via de Responses API met
`reasoning.effort=medium`, Structured Outputs, `store:false`, maximaal 8.000
inputtokens en 800 outputtokens per groep. De applicatie rekent met de
[officiële modelprijzen](https://developers.openai.com/api/docs/models/gpt-6-luna)
van $0,10 per miljoen inputtokens en $0,50 per miljoen outputtokens en stopt
standaard bij $10 per kalendermaand. Volgens de
[OpenAI Data Controls](https://developers.openai.com/api/docs/guides/your-data)
wordt API-data niet voor training gebruikt; standaard abuse-monitoring kan
maximaal dertig dagen worden bewaard.

De providerinterface houdt ruimte voor een latere lokale
[`gpt-oss-20b`](https://developers.openai.com/api/docs/models/gpt-oss-20b)-provider
op TrueNAS. Die koppeling begint uitsluitend in schaduwmodus; de huidige CPU-
host zonder dedicated GPU is niet de vrijgegeven actieve route.

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

Iedere mislukte uitvoerjob mag na een overeenkomend getypeerd AI-voorstel en
lokale idempotentiecontrole exact één automatische herstelpoging krijgen. Bij
een encoderfout mag het voorstel alleen voor die job één allowlisted
geheugeninstelling kiezen: één SVT-worker op CPU of een kleinere QSV-
surfacebuffer op GPU. Dit geheugenherstel heeft een eigen UI-schakelaar. Alleen
wanneer verificatie aantoont dat het resultaat groter is dan de bron, mag de
lokale policy daarnaast voor die ene job een lagere doelbitrate berekenen.
CRF, HDR, Dolby Vision, encoder, baan en het gedeelde profiel blijven
ongewijzigd. De eerste poging blijft volledig in
`job_poging` bewaard. Mislukt poging twee, dan blijft de fout staan voor
menselijke diagnose.

Een "driver reset" reset nooit een kernel-driver vanuit de webapp. Hij start
hoogstens het optionele GPU-meetproces opnieuw en verwijst voor herstel van de
host naar de beheerder. De QSV-test is een korte niet-destructieve test die
de renderer, VAAPI en `av1_qsv` controleert.

## Externe koppelingen

Jellyfin pauzeert desgewenst de CPU-baan bij een echte afspeelsessie. Radarr en
Sonarr herlezen na een geslaagde vervanging uitsluitend het item dat op pad is
gevonden; zij starten nooit een bibliotheekbrede herindexering. API-sleutels
zijn geheimen: versleuteld bewaren, nooit in logs of in een browserantwoord.
