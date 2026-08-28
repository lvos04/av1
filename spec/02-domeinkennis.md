# Domeinkennis — de vallen die je anders pas op je TV ontdekt

**Dit is het belangrijkste document van het pakket.** De architectuur kun je
onderweg bijstellen; deze vijftien punten kosten je bestanden als je ze niet
weet. Ze zijn allemaal empirisch vastgesteld op echte bestanden, niet uit
documentatie overgeschreven.

Lees dit vóór je een regel code schrijft.

> **Eén harde eis:** ffmpeg **7.1 of nieuwer**. Punt 11 werkt niet in 7.0.
> Ubuntu 26.04 levert FFmpeg 8; Ubuntu 24.04 zit op 6.1 en is dus te oud.

---

## 1. HDR10-metadata zit niet waar je hem zoekt

`ffprobe -show_streams` geeft je `side_data_list` op streamniveau. Op de
**meeste** HDR10-bestanden staat de mastering display daar niet in, want die
wordt als SEI in de bitstream meegedragen en komt dus in **frame**-side-data
terecht.

Wie alleen streams leest, ziet op een groot deel van zijn bibliotheek geen
HDR, encodeert het er stilzwijgend uit, en merkt dat pas op zijn TV — nadat er
vijfhonderd bestanden doorheen zijn.

```bash
# Zo lees je beide niveaus:
ffprobe -show_streams -select_streams v:0 bestand.mkv
ffprobe -select_streams v:0 -read_intervals "%+#120" \
        -show_frames -show_entries frame=side_data_list bestand.mkv
```

`startcode/probe.py` doet dit al goed. Kopieer die logica, verzin hem niet
opnieuw.

## 2. Er bestaat geen `-master_display` in ffmpeg

Je zoekt naar een ffmpeg-vlag die er niet is. Voor SVT-AV1 gaat het via
`-svtav1-params`, met decimale coördinaten en luminantie in nits:

```
-svtav1-params mastering-display=G(0.2650,0.6900)B(0.1500,0.0600)R(0.6800,0.3200)WP(0.3127,0.3290)L(1000.0000,0.0001):content-light=1000,400
```

Let op de volgorde: **G, B, R, WP, L**.

## 3. `av1_qsv` kan HDR-metadata helemaal niet meedragen

Controleer het zelf:

```bash
ffmpeg -h encoder=av1_qsv | grep -i master     # geeft niets terug
```

Een HDR10-bestand door de Intel Arc halen levert een bestand op dat er als SDR
uitziet, **zonder foutmelding**. Daarom: HDR gaat naar de CPU, tenzij je de
metadata achteraf met mkvmerge terugzet (punt 4).

## 4. mkvmerge wil een ándere kleurvolgorde dan de encoders

Matroska kan HDR-metadata op containerniveau dragen, en ffprobe leest hem
daarna gewoon weer uit. Getest: alle tien de waarden komen exact terug. Maar:

| Gereedschap | Volgorde |
|---|---|
| SVT-AV1, x265 | **G**roen, **B**lauw, **R**ood |
| mkvmerge | **R**ood, **G**roen, **B**lauw |

Verwissel je die, dan krijg je een bestand dat er in de metadata correct
uitziet maar verkeerde primaries claimt. Je ziet het niet in `mediainfo` en je
ziet het nauwelijks op je TV — maar de kleuren kloppen niet.

`startcode/commandos.py:herstel_hdr()` heeft dit goed.

## 5. Dolby Vision profiel 5 mag je nooit aanraken

| Profiel | Herkomst | Wat je ermee kunt |
|---|---|---|
| 5 | streamingdiensten | **altijd overslaan** |
| 7 | UHD Blu-ray rips | eerst naar 8.1, dan encoden |
| 8.1 | web-releases | rechtstreeks naar AV1 profiel 10 |

Bij 7 en 8.1 zit er een gewone HDR10-laag onder, dus een speler zonder DV toont
correct beeld. **Profiel 5 heeft die laag niet.** Het beeld staat in IPT-PQ-c2,
een eigen kleurruimte, en er bestaat geen correcte niet-DV-interpretatie van.
Haal je de DV eruit, dan krijg je paars-groene onzin.

Dit is geen voorzichtigheid. Er is geen manier om het goed te doen.

## 6. HDR10+ overleeft geen hercodering

Dynamische metadata (ST 2094-40) zit per scène in de bitstream. Geen enkele
AV1-encoder draagt die mee. Detecteer het (zoek `2094-40` in de frame-side-data)
en sla het bestand over.

## 7. Dolby Vision profiel 7 vraagt drie commando's achter elkaar

```
ffmpeg   -> ruwe HEVC eruit trekken (bsf: hevc_mp4toannexb)
dovi_tool -> -m 2 convert --discard   (dual layer naar 8.1)
mkvmerge -> video terugmuxen met de originele audio
```

Bij die laatste stap is `--default-duration 0:<fps>p` **verplicht**: ruwe HEVC
heeft geen timing, en zonder die vlag loopt je audio uit de pas.

En: ruim de werkmap op. De ruwe HEVC-stroom van een UHD-bron is tientallen
gigabytes. Vergeet je dat, dan loopt je schijf vol na een stuk of tien films.

## 8. `mov_text` past niet in Matroska

MP4-bestanden gebruiken `mov_text` voor ondertitels. Remux je die naar MKV met
`-c copy`, dan faalt de hele bewerking:

```
[matroska] Subtitle codec 94213 is not supported.
```

Zet ze om naar SubRip. En let op de **volgorde van je vlaggen**: `-c:s:0 srt`
moet ná `-c copy` staan, anders overschrijft die generieke vlag hem weer.
ffmpeg waarschuwt daarvoor, maar in een log van duizend regels leest niemand
dat.

Wat Matroska wél aankan: `subrip`, `ass`, `ssa`, `webvtt`, `dvd_subtitle`,
`hdmv_pgs_subtitle`, `dvb_subtitle`.

## 9. Anime-ondertitels hangen aan lettertypen die als attachment meereizen

Een ASS-ondertitel verwijst naar lettertypen die als **attachment** in het
bestand zitten. Filter je attachments weg als "rommel", dan valt de speler
terug op een standaardlettertype en is de hele typografie weg — precies bij de
map waar je waarschijnlijk mee begint.

Blijft er een ASS- of SSA-spoor over, map dan `0:t?` mee. Het vraagteken
voorkomt een fout als er toch geen attachments zijn.

## 10. Bitrate meten op het hele bestand is misleidend

`bestandsgrootte / speelduur` telt de audio mee. Een UHD-remux met TrueHD Atmos
en DTS-HD MA draagt zomaar 15 Mbit/s aan audio. Zo'n bestand komt boven je
drempel uit op zijn **audio** in plaats van op zijn beeld.

En het wordt erger als je eerst opschoont: haal je 15 GB audio weg en meet je
daarna nog steeds op het origineel, dan klopt er niets meer van.

In Matroska staat bijna nooit een bitrate per spoor. `startcode/probe.py` meet
daarom de pakketgroottes over een venster uit het **midden** van het bestand —
aan het begin staan logo's en zwart beeld die zo goed comprimeren dat je de
film onderschat.

## 11. `-dolbyvision` moet expliciet op 1, "auto" gooit je DV weg

Sinds ffmpeg 7.1 schrijft `libsvtav1` de Dolby Vision-RPU zelf weg. Daarmee is
HandBrake voor deze tak niet meer nodig — een flinke vereenvoudiging.

Maar de standaardstand is `auto`, en die schrijft de RPU **niet** weg.
Empirisch vastgesteld op een echt profiel 8.1-bestand:

| Commando | Resultaat |
|---|---|
| `-c:v libsvtav1` (auto) | `dv_profile = 0` — **Dolby Vision weg** |
| `-c:v libsvtav1 -dolbyvision 1` | `dv_profile = 10` — behouden |

Geen waarschuwing, geen foutmelding, geen verschil in de logs. Precies het
soort val waar dit document voor bestaat.

`libx265` heeft dezelfde optie en dezelfde standaardstand.

**Minimaal ffmpeg 7.1.** In 7.0 bestaat de optie nog niet. Controleer met:

```bash
ffmpeg -h encoder=libsvtav1 | grep -i dolby
```

## 12. QSV wil `p010le`, niet `yuv420p10le`

Geef je het verkeerde 10-bits formaat mee, dan valt ffmpeg terug op 8-bit of
faalt de encode. Voeg ook een expliciete upload toe:

```
-vf format=p010le,hwupload=extra_hw_frames=64
```

Zonder die filter valt ffmpeg bij sommige bronformaten terug op software — en
dan draait je "GPU-encode" op de CPU, twintig keer trager, zonder dat iets je
waarschuwt.

## 13. Data-sporen breken je mux

`-map 0` neemt ook data-sporen mee, en die kopiëren niet naar Matroska. Sluit
ze uit met `-map -0:d`.

## 14. Verander nooit de bestandsextensie

Uit `Film (2024).mkv` moet weer `Film (2024).mkv` komen. Ga je naar `.mp4`, dan
breken je `.nfo`, je externe `Film (2024).nl.srt` en je Radarr-koppeling in één
keer. Schrijf altijd MKV, ook als de bron iets anders was, en behoud de
bestandsnaam.

## 15. Hardlinks maken je besparing ongedaan

Seed je torrents en importeert Radarr met hardlinks, dan wijzen twee namen naar
dezelfde data. Vervang je het bestand door een kleinere versie, dan blijft het
oude bestand bestaan voor de torrent — en verbruik je **meer** dan voorheen.

```bash
stat -c '%h %n' /pad/naar/film.mkv
```

Staat er `1`, dan speelt het niet. Staat er `2` of meer, dan hangt er een
torrent aan en moet je eerst beslissen wat je met seeden doet.

Toon dit in de UI als een badge per bestand. Het is de enige manier waarop dit
project je netto ruimte kan **kosten**.

---

## Twee dingen over hoe je hiermee werkt

**Je feedbackloop is een encode.** Elke bug die je fixt kost een hercodering om
te verifiëren, en bij 4K op de CPU is dat uren. Gebruik
`test/maak-testbestanden.sh`: bestanden van zes seconden met alle eigenschappen
die ertoe doen. Een testronde wordt dan twintig seconden in plaats van een
avond.

**"Het commando ziet er goed uit" is geen test.** De enige test die telt is:
draai het, en lees de metadata van het resultaat terug. Alle vijftien punten
hierboven falen stil. `test/test_commandos.py` doet het zo.
