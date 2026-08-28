# Fase 7 — Frontend: beslissingen, library, profielen, logs, fouten

**Doel:** de rest van de interface, met het beslissingenscherm als
zwaartepunt.

**Duur:** 5-7 uur.

---

## Lees eerst

- `spec/05-ui.md` — schermen 3, 5, 6, 7 en 8
- `spec/04-veiligheidsregels.md` regel 1
- `spec/07-bediening-en-beheer.md`
- `spec/09-figma-afstemming.md`

## Opdracht

**Scherm 3 — Beslissingen** (`/beslissingen`) — *bouw dit als eerste*

Dit scherm is de reden dat dit project veiliger is dan wat je kant-en-klaar
krijgt. Gegroepeerd op besluit, niet op bestand, met per groep het aantal, de
actie, de baan en de reden. Openklappen geeft de bestandslijst.

Moet erin:

- alles goedkeuren of afwijzen per groep, en per bestand overrulen
- **elke `opruimen`-groep rood gemarkeerd** — dat is het enige dat bestanden
  weggooit, en het moet niet lijken op de andere rijen
- de badge "leek een extra, maar blijft staan" met de reden van het tweede slot
- een hardlink-badge bij `nlink > 1`, met de uitleg dat vervangen hier geen
  ruimte oplevert zolang de torrent seedt
- een bevestigingsdialoog bij goedkeuren van `opruimen` die het **aantal en de
  totale grootte** noemt. Niet "weet je het zeker?" maar "41 bestanden, 3,2 GB,
  permanent verwijderen"

**Scherm 5 — Library** (`/library`) — per library een voortgangsbalk, de
knoppen scannen en droogloop, en het profiel wisselen.

**Scherm 6 — Profielen** (`/profielen`) — formulier per instelling, met bij
elke instelling **wat hij doet**, niet alleen zijn naam. Bij `preset` hoort te
staan dat preset 6 op 4K vijf tot acht uur per film kost; bij `gpu_voor_hdr10`
dat av1_qsv geen mastering display kan meedragen en de metadata er achteraf bij
gemuxt wordt.

**Scherm 7 — Logs** (`/logs`) — live tail per job via de WebSocket (abonneren
met `volg`), doorzoekbaar archief, filter op niveau, en een knop "kopieer
commando".

**Scherm 8 — Fouten** (`/fouten`) — gegroepeerd op foutsoort. Per fout het
bestand, de reden, het commando, de laatste 50 regels uitvoer, en opnieuw
proberen met één klik.

Maak ook de links naar Kwaliteit en Instellingen zichtbaar. De
volledige implementatie daarvan volgt in fase 10, maar de navigatie, donkere
modus en veilige taal mogen nergens later worden vergeten: geen "forceer" bij
HDR10+ en geen tekst die suggereert dat hardlinks hersteld kunnen worden.

## Acceptatietest

- een `opruimen`-groep goedkeuren vraagt om bevestiging met aantal en grootte
- een bestand overrulen van GPU naar CPU verandert werkelijk de job
- de live tail loopt mee tijdens een encode en stopt bij het afsluiten van het
  scherm (afmelden bij de WebSocket)
- tien identieke fouten worden als één groep getoond

## Niet doen

Geen goedkeuring-in-één-klik voor `opruimen` zonder bevestiging, hoe vaak je
het ook doet.
