# Opslag, herstel en schakelaars

De 1 TB-SSD is de snelle werkplek van de pipeline. Hij is niet de enige plek
waarvan een kopie telt; vervangen originelen worden na verificatie permanent
verwijderd.

## Indeling van de 1 TB-SSD

Koppel de SSD vast aan `/srv/av1-pipeline` en geef de backendgebruiker toegang
tot de vier submappen:

| Map | Doel | Mag automatisch worden opgeruimd |
|---|---|---|
| `cache/` | resultaten tijdens encode | alleen afgebroken/mislukte tijdelijke output |
| `work/` | DV-, VMAF- en proefdraaiwerk | ja, na een gecontroleerde job |
| `config/` | SQLite, configuratie en logs | nee |
| `backups/` | recente lokale back-ups | volgens de ingestelde bewaartermijn |

De planner reserveert vóór een job `CACHE_RESERVE_FACTOR` maal de brongrootte
op de SSD en telt reserveringen van lopende CPU- en GPU-jobs bij elkaar op. Met
1 TB is één CPU- en één GPU-job meestal realistisch; de instelling
`MAX_ACTIVE_JOBS=2` is daarom de veilige start. Verhoog pas na metingen op
jouw grootste bestanden.

## Vervangen en herstel

1. Encodeer uitsluitend naar de SSD-cache.
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
| LAN/externe toegang | uit | alleen lokale toegang; activeren vereist wachtwoord/proxy |
| Hardlink verbreken | uit | hardlinks worden weer standaard overgeslagen |
| Experimenteel Dolby Vision | uit | DV blijft volgens profiel overslaan |
| Automatische back-ups | aan | maakt geen nieuwe schema-back-ups; bestaande blijven staan |
| Canary-limiet | aan | maakt gecontroleerde batches mogelijk; `uit` vereist extra bevestiging |

De volgende zaken zijn geen schakelaars: goedkeuring vóór uitvoering,
metadata-verificatie, behoud van bestandsrechten, mountcontrole en
hersteltransacties. Ze beschermen gegevens en blijven actief.

## Back-up en toegang

Automatische back-ups bevatten SQLite, de niet-geheime configuratie en een
hashmanifest. De geheime sleutel staat apart in
`/etc/av1-pipeline-secrets.key`; maak daarvan een versleutelde offline kopie.
De UI biedt een **herstelcontrole** die een back-up naar een tijdelijke SQLite-
database kopieert en opent, zonder de werkende database te wijzigen.

De webapp bindt standaard alleen lokaal. Toegang vanaf je thuisnetwerk is een
optionele functie met een reverse proxy, TLS waar mogelijk en een apart
wachtwoord. De backend zelf blijft op `127.0.0.1` gebonden.

## Canary-modus

Canary is de standaard voor een nieuwe library of na een update. Stel een
maximum in voor bestanden, GB en jobs per dag. Na een batch toont de app een
rapport met metadata-, speelbaarheids- en ruimte-uitkomsten. Pas na expliciete
goedkeuring mag de library naar normale planning. De testbibliotheek bevat
korte representatieve fragmenten van HDR10, HDR10+, Dolby Vision, hardlinks,
ASS-lettertypen, mov_text en meerdere audioformaten; regressietests draaien
bij elke wijziging aan media-code of FFmpeg.
