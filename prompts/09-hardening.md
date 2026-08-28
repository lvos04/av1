# Fase 9 — Loslaten op 50 TB

**Doel:** het draait weken achter elkaar zonder dat jij ernaar kijkt.

**Duur:** 3-4 uur, plus een week waarin je het in de gaten houdt.

---

## Opdracht

**Herstarten en herstellen**

- jobs die op `bezig` staan zonder levend proces terugzetten naar `wacht`
- cachemappen van afgebroken jobs opruimen bij het opstarten
- half geschreven bestanden in de cache mogen nooit als resultaat gelden:
  schrijf naar `<naam>.deel` en hernoem pas na een geslaagde encode

**Schijfruimte**

- vóór elke job controleren of er in `CACHE_ROOT` minstens 3× de
  bronbestandsgrootte vrij én niet al gereserveerd is; zo niet: job blijft
  wachten, en de UI meldt waarom
- waarschuwen zodra `CACHE_ROOT` onder de 50 GB komt
- automatisch pauzeren onder de 10 GB — vollopen midden in een encode is de
  vervelendste manier om erachter te komen

**Logrotatie**

- per job een eigen logbestand in `CONFIG_ROOT/logs/`
- ouder dan 30 dagen opruimen, en van geslaagde jobs alleen de laatste 200
  regels bewaren
- in de database maximaal N regels per job; de rest staat op schijf

**Database**

- een `VACUUM` en `PRAGMA optimize` per week
- een backup van `app.db` vóór elke migratie
- indexen controleren zodra `bestand` boven de 100.000 rijen komt: de
  wachtrijquery mag geen seconden duren

**Fouten**

- een job die drie keer op dezelfde fout stukloopt gaat naar `mislukt` en wordt
  niet meer automatisch geprobeerd
- foutsoorten groeperen: tien keer "geen ruimte" is één probleem, niet tien
- optioneel een melding (ntfy, Gotify, e-mail) bij een baan die langer dan een
  uur stilstaat terwijl er werk ligt
- browsermeldingen voor fouten en afgeronde batches, uitsluitend nadat de
  gebruiker browserrechten heeft gegeven; het dashboard blijft volledig bruik-
  baar als de browser dit weigert

**Naast Unmanic draaien**

Draait Unmanic nog op dezelfde bestanden, dan willen twee processen hetzelfde
bestand vervangen. Los dat zo op:

- vlak vóór de vervanging controleren of `mtime` en `grootte` nog gelijk zijn
  aan wat er bij het scannen stond; zo niet, afbreken
- of simpeler: laat ze nooit dezelfde library aanraken. Zet Unmanic op je
  anime-map en je eigen pipeline op je films, en wissel pas als je de laatste
  vertrouwt

## Acceptatietest

Een week meekijken op een echte library. Wat er moet kloppen:

- geen enkel bestand verloren of beschadigd
- `CACHE_ROOT` loopt niet vol
- de backendservice overleeft een herstart midden in een 4K-encode
- de besparingsteller klopt met wat `du -sh` zegt
- de wachtrij opent binnen een seconde bij 100.000 bestanden

## En dan

Zet je eerste vijftig hercodeerde bestanden op je TV en kijk ernaar. Niet
steekproefsgewijs de metadata controleren — werkelijk kijken, een paar
minuten, op het scherm waar je ze uiteindelijk voor bedoeld hebt.

Dat is de enige test die niemand voor je kan doen, en de enige die telt.
