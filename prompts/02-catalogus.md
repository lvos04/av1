# Fase 2 — Catalogus: scannen en besluiten opslaan

**Doel:** een hele library kunnen scannen, alles in de database, en de
beslissingen kunnen inzien. Nog geen uitvoering.

**Duur:** 3-4 uur.

---

## Lees eerst

- `spec/03-datamodel.md`
- `spec/04-veiligheidsregels.md` — vooral regel 1, 6 en 9

## Opdracht

**Scanner** (`app/scanner.py`):

- loopt door het pad van een library
- slaat bestanden over die geen videoformaat zijn
- past `bestandsfilter.is_hoofdbestand()` toe en bewaart de categorie
- probet alleen wat categorie `ok` of `extra` heeft
- registreert `nlink` (hardlinks, veiligheidsregel 9)
- schrijft `bestand`-rijen weg, en werkt bestaande rijen bij als `mtime` of
  `grootte` is veranderd
- verwijdert rijen van bestanden die niet meer bestaan

Moet **hervatbaar** zijn: 50 TB scannen duurt uren en er gaat een keer iets
mis. Werk per map, commit onderweg, en onthoud waar je was.

**Beslisser** (`app/beslisser.py`):

- neemt de probe plus het profiel van de library
- maakt een `besluit`-rij met status `nieuw`
- voor `categorie = extra`: draai óók `veilig_om_op_te_ruimen()`, en maak
  alleen een `opruimen`-besluit als dat groen is. Wordt het tegengehouden, maak
  dan een besluit `overslaan` met de reden van het tweede slot, zodat je in de
  UI ziet dat het slot werkt

**CLI:**

```bash
python -m app.cli scan <library-id> [--dry-run]
python -m app.cli besluiten <library-id> [--status nieuw]
python -m app.cli goedkeuren <library-id> --actie hercoderen
```

`--dry-run` schrijft niets weg en print alleen — dit is de droogloop waarmee
je je bibliotheek keurt vóór er iets in de database komt.

## Acceptatietest

```bash
python -m app.cli scan 1 --dry-run
```

Op `testdata/mappen/` moet dit precies dit geven:

| Bestand | Verwacht |
|---|---|
| `Dune.Part.Two.2024.2160p.mkv` | ok |
| `Dune Part Two (2024)-trailer.mkv` | extra, mag weg (kleiner dan de film ernaast) |
| `Trailer.Park.Boys.S01E01.1080p.mkv` | **ok** — geen trailer |
| `Short.Term.12.2013.1080p.BluRay.x264.mkv` | **ok** — geen short |
| `Film Met Extras (2021)/Extras/Making Of.mkv` | extra, mag weg (extra-map) |

Gaat een van die laatste drie fout, dan is de filter aangetast. Stoppen en
teruggaan naar `bestandsfilter.py`.

Verder: twee keer scannen mag geen dubbele rijen geven, en een scan afbreken
en hervatten moet werken.

## Niet doen

De scanner mag **niets** aanraken: niet hernoemen, niet verplaatsen, niet
verwijderen. Hij leest en schrijft naar de database. Meer niet.
