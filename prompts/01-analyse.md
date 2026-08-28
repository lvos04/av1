# Fase 1 — Analyse: probe, beslissen, commando's

**Doel:** gegeven een bestandspad weten wat erin zit en wat ermee moet
gebeuren. Nog steeds geen database, geen UI. Alles via de CLI.

**Duur:** 2-3 uur — grotendeels overnemen en testen, niet bedenken.

---

## Lees eerst

- **`spec/02-domeinkennis.md` — helemaal, dit is de kern**
- `startcode/README.md`

## Opdracht

De modules in `startcode/` zijn getest tegen echte bestanden en werken. Neem ze
over in `backend/app/media/`:

| Uit startcode | Naar | Wat het doet |
|---|---|---|
| `probe.py` | `media/probe.py` | ffprobe op twee niveaus, HDR/DV-detectie |
| `beslissen.py` | `media/beslissen.py` | de beslisboom |
| `commandos.py` | `media/commandos.py` | ffmpeg- en mkvmerge-commando's |
| `bestandsfilter.py` | `media/bestandsfilter.py` | trailers en extra's herkennen |
| `voortgang.py` | `media/voortgang.py` | voortgang uit de uitvoer parsen |

**Verander de logica niet.** Elk van die functies dekt een valkuil af die in
`spec/02-domeinkennis.md` staat uitgelegd. Wat je wél doet:

- typehints en docstrings aanvullen
- `Besluit` een `pydantic`-model maken in plaats van een dataclass, zodat hij
  rechtstreeks door de API kan
- instellingen uit een profiel-dict halen in plaats van uit `STANDAARD`
- de tests uit `test/` overnemen in `backend/tests/`

Bouw daarna een CLI:

```bash
python -m app.cli probe   <bestand>            # JSON van de probe
python -m app.cli beslis  <bestand> [profiel]  # het besluit met reden
python -m app.cli commando <bestand>           # het commando dat zou draaien
```

## Acceptatietest

```bash
./test/maak-testbestanden.sh testdata
python -m pytest backend/tests/ -v
```

**Alle tests groen.** In het bijzonder:

- `test_hdr10_wordt_herkend` — mastering display uit frame-side-data
- `test_cpu_encode_behoudt_hdr10` — draait een echte encode en leest de
  metadata terug
- `test_mp4_met_movtext_faalt_niet` — mov_text wordt omgezet
- `test_echte_films_worden_niet_als_extra_gezien` — twaalf realistische namen

Daarna handmatig:

```bash
python -m app.cli beslis testdata/hdr10.mkv
# -> hercoderen, cpu, libsvtav1, behoud_hdr=true
```

## Niet doen

Geen eigen probe-logica schrijven "omdat het simpeler kan". De reden dat
`probe.py` frame-side-data leest staat in `spec/02-domeinkennis.md` punt 1, en
je merkt het verschil pas op je TV.
