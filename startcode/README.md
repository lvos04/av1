# Startcode

Vijf modules die al werken, getest tegen echte bestanden. Dit is het verschil
tussen twintig uur bouwen en vijfendertig.

| Module | Wat het doet | Getest met |
|---|---|---|
| `probe.py` | ffprobe op stream- én frame-niveau, HDR10/DV/HDR10+-detectie, videobitrate | echte HDR10-bron, alle 10 mastering-displaywaarden kloppen |
| `beslissen.py` | de beslisboom: overslaan, opschonen of hercoderen, en op welke baan | 9 tests, inclusief het veiligheidsnet voor audio |
| `commandos.py` | ffmpeg- en mkvmerge-commando's, inclusief Dolby Vision | encodes werkelijk gedraaid, DV 8.1 -> AV1 profiel 10 teruggelezen |
| `bestandsfilter.py` | trailers en extra's herkennen, plus het tweede slot | 18 realistische bestandsnamen |
| `voortgang.py` | fps, ETA, bitrate en grootte uit de uitvoer parsen | gemeten 0 → 100 op een echte encode |

## Eén harde eis

**ffmpeg 7.1 of nieuwer.** Vanaf die versie schrijft `libsvtav1` de Dolby
Vision-RPU zelf weg; daarvoor niet. Controleer met:

```bash
ffmpeg -h encoder=libsvtav1 | grep -i dolby
```

Ubuntu 26.04 levert FFmpeg 8. Ubuntu 24.04 zit op 6.1.

## Zelfstandig uitproberen

```bash
python3 probe.py     /pad/naar/film.mkv
python3 beslissen.py /pad/naar/film.mkv
```

Geen afhankelijkheden buiten de standaardbibliotheek en `ffprobe` op je PATH.

## Waarom dit meekomt

De moeilijke helft van dit project is niet de wachtrij, de workers of het
dashboard — dat is gewoon werk. De moeilijke helft is weten dat mastering
display in frame-side-data zit, dat `av1_qsv` die metadata niet kan dragen, en
dat mkvmerge de kleuren in een andere volgorde wil dan SVT-AV1.

Die kennis zit in deze vijf bestanden en in `spec/02-domeinkennis.md`. Begin je
blanco, dan ontdek je ze opnieuw — en meestal pas op je TV.

## Wat je wél mag veranderen

- typehints en docstrings aanvullen
- `Besluit` een pydantic-model maken zodat hij door de API kan
- instellingen uit een profiel halen in plaats van uit `STANDAARD`
- logging aansluiten op je eigen opzet

## Wat je niet moet veranderen

De beslisboom zelf, de volgorde van de mkvmerge-argumenten, en het lezen van
frame-side-data. Als iets er omslachtig uitziet, staat de reden in
`spec/02-domeinkennis.md`.
