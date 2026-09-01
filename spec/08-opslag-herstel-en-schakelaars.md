# Opslag, herstel en schakelaars

De host heeft standaard één 1-TB-systeemschijf. Ubuntu, SQLite, logs, cache en
werkbestanden delen die schijf, maar de pipeline houdt daarop altijd een harde
vrije-ruimtebodem vrij. Een later toegevoegde schijf kan in de UI als
werkruimte worden gekozen nadat Ubuntu hem heeft gemount; formatteren en
mounten blijven uitdrukkelijk beheerhandelingen buiten de pipeline.

Dit verbod geldt voor werk- en cacheschijven: de UI formatteert, partitioneert
of mount die nooit. Bibliotheekbronnen zijn anders. Een expliciet ingevoerd
`smb://server/share/map` mag via de geïnstalleerde, streng begrensde helper een
CIFS-automount onder `/library/netwerk` krijgen. De helper is root-owned,
accepteert alleen privé-netwerkservers en heeft `nosuid,nodev,noexec`; FastAPI
zelf blijft zonder rootrechten.

## Vaste control-plane en gekozen werkruimte

`/srv/av1-pipeline/config` blijft altijd op de systeemschijf. Daardoor blijven
de database, hersteltransacties en opslagpagina bereikbaar als een latere
werkschijf ontbreekt. De standaardindeling is:

| Map | Doel | Mag automatisch worden opgeruimd |
|---|---|---|
| `/srv/av1-pipeline/cache/` | resultaten tijdens encode | alleen afgebroken/mislukte tijdelijke output |
| `/srv/av1-pipeline/work/` | DV-, VMAF- en proefdraaiwerk | ja, na een gecontroleerde job |
| `/srv/av1-pipeline/config/` | SQLite, opslagkeuze en logs | nee |
| `/srv/av1-pipeline/backups/` | recente lokale herstelkopieën | volgens de ingestelde bewaartermijn |

De planner reserveert vóór een job `CACHE_RESERVE_FACTOR` maal de brongrootte
en telt reserveringen van lopende CPU- en GPU-jobs bij elkaar op. In
systeemschijfmodus moet daar standaard nog minimaal 150 GB werkelijk vrij
boven blijven; de UI kan die bodem verhogen, maar niet onder 150 GB brengen.
Waarschuwen begint bij 250 GB. Met 1 TB is één CPU- en één GPU-job meestal
realistisch. De actuele totale encodeconcurrency staat in SQLite, begint op 1
voor de huidige 15-GiB-host en kan na een RAM-upgrade zonder vaste
applicatiebovengrens worden verhoogd. Cache- en mountcontroles blijven iedere
extra claim afzonderlijk begrenzen.

De pagina **Instellingen → Opslag kiezen** biedt twee modi:

- **1-TB-systeemschijf:** cache en werk onder `/srv/av1-pipeline`, met de
  niet-uitschakelbare systeembodem;
- **aparte gemounte werkschijf:** cache en werk samen onder één gekozen,
  bestaand mountpoint. Nieuwe back-ups mogen naar keuze op de systeemschijf of
  die werkruimte komen.

Een wissel pauzeert beide workers en kan alleen zonder actieve jobs,
achtergrondproeven of achtergebleven cache-/werkbestanden. Het nieuwe pad moet
een echt afzonderlijk filesystem zijn, mag geen symlink zijn, mag niet binnen
`/library` liggen en moet eigendom van de backendgebruiker zijn. De UI voert
eerst een droge controle uit, schrijft de keuze atomisch naar
`config/opslag.json` en raakt geen partitie- of mountconfiguratie aan.

## Vervangen en herstel

1. Encodeer uitsluitend naar de gekozen cache.
2. Verifieer daar het resultaat.
3. Leg een `vervang_transactie` vast en kopieer het resultaat als verborgen
   tijdelijk bestand naar de doelmap op de mediavolume.
4. Herstel eigenaar, groep, mode, ACL's en xattrs; `fsync` bestand en map.
5. Wissel de tijdelijke bestandsnaam atomisch naar het oorspronkelijke pad;
   die wissel verwijdert de geverifieerde bron permanent. `fsync` opnieuw en
   markeer de transactie als klaar.

Bij iedere start loopt herstel vóór de planner. Een incomplete transactie wordt
zichtbaar in de interface en veilig afgemaakt wanneer bron of resultaat
ondubbelzinnig aanwezig is. Een bronpad dat ondertussen door iemand anders is
gewijzigd, of een niet te bewijzen toestand, wordt niet aangeraakt maar als
conflict aangeboden.

## Schakelaars in de interface

De pagina **Instellingen → Onderdelen** toont één lijst met uitleg, status,
eventuele benodigde gegevens en een schakelaar. Een uitschakeling stopt nieuw
werk van dat onderdeel, ruimt geen data op en verandert geen geslaagde jobs.

| Onderdeel | Standaard | Uitschakelen doet dit |
|---|---:|---|
| GPU-meting | uit | stopt alleen `intel_gpu_top`; QSV-encodes blijven mogelijk |
| VMAF | uit | verbergt nieuwe metingen, bestaande resultaten blijven leesbaar |
| Proefdraai | uit | voorkomt nieuwe proefdraaiopdrachten |
| Browsermeldingen | uit | stuurt geen browsermeldingen |
| Jellyfin-pauze | uit | negeert afspeelsessies; CPU-planner werkt normaal |
| Radarr | uit | stuurt geen film-refreshes |
| Sonarr | uit | stuurt geen serie-refreshes |
| LAN/externe toegang | uit | de LAN-gateway weigert andere apparaten; activeren vereist een apart wachtwoord |
| Hardlink verbreken | uit | hardlinks worden weer standaard overgeslagen |
| Experimenteel Dolby Vision | uit | DV blijft volgens profiel overslaan |
| Automatische back-ups | aan | maakt geen nieuwe schema-back-ups; bestaande blijven staan |
| Canary-limiet | aan | maakt gecontroleerde batches mogelijk; `uit` vereist extra bevestiging |
| AI-controller | uit, schaduw gereed | stopt nieuwe AI-analyses; besluiten en historie blijven staan |

De volgende zaken zijn geen schakelaars: de goedkeurings-/policy-poort vóór uitvoering,
metadata-verificatie, behoud van bestandsrechten, mountcontrole en
hersteltransacties. Ze beschermen gegevens en blijven actief.

## Back-up en toegang

Automatische back-ups bevatten SQLite, de niet-geheime configuratie en een
hashmanifest. Op dezelfde systeemschijf zijn dit herstelkopieën tegen een
databasefout, geen bescherming tegen fysiek schijfdefect. De geheime sleutel staat apart in
`/etc/av1-pipeline-secrets.key`; maak daarvan een versleutelde offline kopie.
De UI biedt een **herstelcontrole** die een back-up naar een tijdelijke SQLite-
database kopieert en opent, zonder de werkende database te wijzigen.

De FastAPI-backend bindt altijd uitsluitend op `127.0.0.1`. De ingebouwde
frontend-gateway luistert op poort 3000, maar weigert niet-lokale verzoeken
zolang LAN-toegang uitstaat. Na expliciete bevestiging laat hij alleen
privé-adressen door met HTTP Basic-authenticatie, een geheugenhard gehasht
wachtwoord, tijdelijke blokkade na herhaalde fouten en dezelfde afscherming
voor de WebSocket. Stuur deze poort niet door in de router; gebruik voor
toegang buiten het thuisnetwerk een VPN en TLS.

## Canary-modus

Canary is de standaard voor een nieuwe library of na een update. Stel een
maximum in voor bestanden, GB en jobs per dag. Na een batch toont de app een
rapport met metadata-, speelbaarheids- en ruimte-uitkomsten. Pas na expliciete
goedkeuring mag de library naar normale planning. De testbibliotheek bevat
korte representatieve fragmenten van HDR10, HDR10+, Dolby Vision, hardlinks,
ASS-lettertypen, mov_text en meerdere audioformaten; regressietests draaien
bij elke wijziging aan media-code of FFmpeg.

Na canary-vrijgave scant de backend actieve libraries intern en incrementeel.
Nieuwe of gewijzigde media wacht minstens zestig minuten en moet vóór en na
`ffprobe` exact hetzelfde device, inode, formaat en mtime hebben. Verdwenen
media blijft als historie bestaan. Een profiel- en besliscodefingerprint zorgt
dat alleen relevante wijzigingen opnieuw worden beoordeeld; een geslaagde
atomische vervanging schrijft de nieuwe baseline direct terug.
