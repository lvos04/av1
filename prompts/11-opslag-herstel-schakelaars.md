# Fase 11 — SSD, herstel en beheersbare onderdelen

**Doel:** maak de 1 TB-project-SSD, vervanging, herstel en optionele functies
echt robuust en eenvoudig beheersbaar.

**Duur:** 5-8 uur.

---

## Lees eerst

- `spec/08-opslag-herstel-en-schakelaars.md` — volledig
- `spec/04-veiligheidsregels.md` — regels 15 t/m 18
- `spec/03-datamodel.md`
- `prompts/03-uitvoeren.md` en `prompts/04-wachtrij-workers.md`
- `spec/09-figma-afstemming.md`

## Opdracht

### 1. De eigen 1 TB-SSD

Gebruik uitsluitend `/srv/av1-pipeline` als `PIPELINE_ROOT`. Controleer bij
start én vóór iedere job:

- dat dit een werkelijk mountpoint is, geen gewone map op de systeemschijf;
- dat `cache`, `work`, `config` en `backups` bestaan en de juiste eigenaar
  hebben;
- dat de SSD schrijfbaar is en genoeg ruimte heeft, inclusief alle bestaande
  reserveringen;
- dat iedere library schrijfbaar is en op het verwachte `st_dev` staat.

De planner reserveert atomisch `CACHE_RESERVE_FACTOR × bron_grootte` per job.
Begin met maximaal twee gelijktijdige jobs op de 1 TB-SSD. Toon vrije ruimte,
gereserveerde ruimte en de reden van een blokkade op het dashboard.

### 2. Crashveilige vervanging

Vervang `shutil.move` of andere cross-volume-verplaatsingen door het protocol
uit `spec/08`: cache → verificatie → verborgen tijdelijk bestand in doelmap →
metadata/ACL/xattr-herstel → `fsync` → atomische rename die de bron permanent
vervangt → `fsync`. Leg alle stappen vast in `vervang_transactie`.

Bij iedere backendstart voer je herstel uit vóór de planner. Open transacties
moeten zichtbaar zijn in een hersteloverzicht. Als inode, mtime of grootte niet
meer kloppen, verander je niets en maak je een conflict met duidelijke opties:
alleen inspecteren, resultaat verwijderen of handmatig herstellen. Nooit een
automatische gok.

### 3. Behoud van bestandstoestand

Sla vóór een job uid, gid, mode, ACL's en relevante xattrs op. Herstel én lees
ze terug op het tijdelijke resultaat vóór de wissel. Schrijf tests met een
extra ACL en ten minste één xattr. Een niet te kopiëren attribute wordt een
veilig falende job, geen waarschuwing die daarna wordt genegeerd.

### 4. Schakelaars

Maak **Instellingen → Onderdelen** vanuit de `functie`-tabel. Gebruik exact de
onderdelen en veilige standaardwaarden uit `spec/08`. Iedere schakelaar heeft:

- een korte uitleg en afhankelijkheden;
- de huidige status en eventuele laatste fout;
- een bevestiging bij inschakelen van LAN-toegang, hardlink verbreken,
  experimenteel Dolby Vision en het uitzetten van de canary-limiet;
- gedrag dat nieuw werk stopt bij uitschakelen, maar niets verwijdert.

Kernveiligheid heeft geen schakelaar. Laat de API elke verandering valideren,
auditloggen en via de WebSocket uitsturen.

### 5. Back-up, toegang en tests

Maak optionele automatische back-ups met hashmanifest en retentie. Voeg een
herstelcontrole toe die een kopie naar een tijdelijke database herstelt en
opent. Toon alleen een geslaagde controle als database en manifest echt kloppen.

Standaard is de app alleen lokaal bereikbaar. Maak LAN-toegang optioneel via
een reverse proxy met wachtwoord; de FastAPI-backend blijft op `127.0.0.1`.
Bewaar geen wachtwoorden of API-sleutels in logs, URL's of frontend-state.

Breid de testbibliotheek uit met korte representatieve bestanden en voer die
bij iedere verandering aan media-code uit. Maak een canary-planner met maxima
voor bestanden, GB en jobs per dag, standaard actief voor nieuwe libraries.

## Acceptatietest

- backend start niet als `/srv/av1-pipeline` geen echte mount is
- twee actieve jobs reserveren samen ruimte; een derde job wacht eerlijk
- een gesimuleerde crash na iedere vervangstap geeft na herstart óf bron óf
  volledig gecontroleerd resultaat terug, nooit een ontbrekend pad
- eigenaar, mode, ACL en test-xattr zijn na een geslaagde vervanging identiek
- elke optionele functie is vanuit één scherm aan en uit te zetten
- uitzetten van een optie start geen nieuw werk, maar verwijdert geen data
- poging om kernveiligheid via API uit te schakelen wordt geweigerd
- herstelcontrole valideert een back-up zonder de werkende database te raken
- LAN-toegang is standaard uit

## Niet doen

- Geen automatische formattering, partitionering of mountwijziging van de SSD.
- Geen cross-volume `rename` als zogenaamde atomische vervanging.
- Geen opslag van geheimen in plaintext in SQLite.
