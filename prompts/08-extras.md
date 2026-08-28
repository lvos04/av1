# Fase 8 — Kwaliteitscontrole en *arr

**Doel:** de dingen die het verschil maken tussen "het draait" en "ik vertrouw
het".

**Duur:** 4-6 uur. Alles hier is optioneel; doe ze in deze volgorde.

---

## 1. Verificatiebadge (1 uur, doe dit sowieso)

Per voltooide job een groen of rood blokje met wat er gecontroleerd is:

```
✓ HDR10 mastering display identiek
✓ content light identiek
✓ 2 audiosporen behouden (was 5)
✓ lettertypen behouden
```

De gegevens staan al in de `verificatie`-tabel uit fase 3. Dit is alleen
weergave — en het is de beste manier om te merken dat je vangnet nog werkt.

## 2. VMAF en voor/na (2 uur)

Twee frames op hetzelfde tijdstip uit bron en resultaat, met een schuifregelaar
ertussen. Frames haal je eruit met:

```bash
ffmpeg -ss <midden> -i <bestand> -frames:v 1 -q:v 2 frame.jpg
```

VMAF over een steekproef van 30 seconden:

```bash
ffmpeg -ss <midden> -t 30 -i resultaat.mkv -ss <midden> -t 30 -i bron.mkv \
  -lavfi "[0:v]scale=1920:1080:flags=bicubic[a];[1:v]scale=1920:1080:flags=bicubic[b];[a][b]libvmaf=n_threads=4" \
  -f null -
```

Draai dit op je eerste vijftig bestanden. Boven de 95 zie je geen verschil,
onder de 90 wel. Zo weet je of je CRF klopt **voordat** je vijftienduizend
bestanden verwerkt.

Controleer of je ffmpeg-build `libvmaf` heeft (`ffmpeg -filters | grep vmaf`);
zo niet, dan is dit onderdeel niet beschikbaar en moet de UI dat melden in
plaats van te falen.

## 3. Proefdraai voor profielen (2 uur)

Kies één bestand, knip er twee minuten uit het midden, encodeer die op CRF 27,
29 en 31, en toon een tabel met grootte, tijd en VMAF.

Dat maakt van "CRF 29 lijkt me wel goed" een onderbouwde keuze, in twintig
minuten in plaats van na drie maanden.

## 4. Pauzeren tijdens kijken (1 uur)

Vraag de Jellyfin-API of er iemand afspeelt:

```
GET /Sessions?api_key=<sleutel>
```

Is er een sessie met `NowPlayingItem`, pauzeer dan de CPU-baan. De GPU-baan kun
je meestal door laten lopen.

De koppeling is opt-in. Bewaar de Jellyfin-sleutel versleuteld, bied een
verbindingstest en een statusregel "CPU gepauzeerd omdat er gekeken wordt".

Niets is vervelender dan een haperende film omdat je eigen pipeline de machine
vult.

## 5. Radarr en Sonarr (1-2 uur)

Na een geslaagde vervanging het item laten herscannen. **Zoek het item eerst op
pad op:**

```
GET  /api/v3/movie          -> match op path
POST /api/v3/command        {"name":"RefreshMovie","movieIds":[<id>]}
```

Stuur je `RefreshMovie` zónder id, dan herscant Radarr je **hele** bibliotheek.
Na elke voltooide encode. Bij 50 TB.

Optioneel ontvolgen na vervanging, zodat Radarr niet op zoek gaat naar een
"betere" release. Bij Sonarr gaat ontvolgen per seizoen — doe dat alleen voor
afgeronde seizoenen, of laat het handmatig.

De koppelingen zijn opt-in, krijgen elk een verbindingstest en loggen per
vervanging welke externe opdracht is gestuurd. Falen van een *arr-rescan mag
een geslaagde vervanging nooit terugdraaien of de job alsnog als fout markeren.

## Acceptatietest

- verificatiebadge klopt met de `verificatie`-tabel
- VMAF geeft een getal tussen 0 en 100 op de testbestanden
- proefdraai levert drie rijen op met oplopende grootte bij lagere CRF
- Radarr-rescan raakt alleen de betreffende film — controleer in het
  Radarr-log dat er niet een volledige scan start
