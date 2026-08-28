# Dolby Vision

Kort antwoord: **de DV-metadata blijft behouden, en je hebt er geen HandBrake
meer voor nodig.**

Sinds ffmpeg 7.1 schrijft `libsvtav1` de Dolby Vision-RPU zelf weg. Dat is hier
geverifieerd op een echt profiel 8.1-bestand: er komt AV1 met **DV profiel 10**
uit, met de HDR10-basislaag, de audio, de ondertitels en de lettertypen intact.

Dat scheelt een bouwstap van veertig minuten, een OS-afhankelijkheid, en een
encoder die geen `-progress pipe:1` kent.

---

## Wat er per profiel gebeurt

| Profiel | Herkomst | Wat de pipeline doet | Gereedschap |
|---|---|---|---|
| 5 | streamingdiensten | **altijd overslaan** | — |
| 7 | UHD Blu-ray rips | dovi_tool naar 8.1, dan ffmpeg | dovi_tool, mkvmerge, ffmpeg |
| 8.1 / 8.4 | web-releases | rechtstreeks ffmpeg | ffmpeg |

Profiel 5 wordt nooit aangeraakt en dat verandert nooit. Het beeld staat in
IPT-PQ-c2 zonder HDR10-basislaag; haal je de DV eruit, dan krijg je
paars-groene onzin. Er is geen manier om het goed te doen.

Profiel 7 is dual layer. ffmpeg kan de enhancement layer niet aan, dus die moet
er eerst met `dovi_tool` af. Dat is een statische musl-binary van een paar MB
die overal draait — geen pakketprobleem.

## De vlag die je niet mag vergeten

```
-c:v libsvtav1 -dolbyvision 1
```

**Die 1 moet er expliciet staan.** De standaardstand is `auto` en die schrijft
de RPU niet weg. Gemeten op hetzelfde bestand:

| Commando | `dv_profile` in het resultaat |
|---|---|
| `-c:v libsvtav1` | **0 — Dolby Vision weg** |
| `-c:v libsvtav1 -dolbyvision 1` | 10 — behouden |

Geen waarschuwing, geen foutmelding. `test_zonder_expliciete_vlag_gaat_dv_verloren`
in de testsuite bewaakt dit, zodat niemand die vlag later "opruimt" omdat hij
overbodig lijkt.

## Wat je native op Ubuntu nodig hebt

| Gereedschap | Waarvoor | Minimaal |
|---|---|---|
| `ffmpeg` met `libsvtav1` | de hele DV-tak | **7.1** |
| `dovi_tool` | profiel 7 naar 8.1 | 2.x |
| `mkvmerge` | de video terugmuxen bij profiel 7 | willekeurig |

Controleren:

```bash
ffmpeg -h encoder=libsvtav1 | grep -i dolby
# -> -dolbyvision  <boolean>  E..V....... Enable Dolby Vision RPU coding
```

Geeft dat niets terug, dan is je ffmpeg ouder dan 7.1 en werkt deze tak niet.
Ubuntu 24.04 zit op 6.1 en is dus te oud; **Ubuntu 26.04 levert FFmpeg 8**.
Zie `prompts/00-scaffold.md`.

## De onzekerheid die je moet kennen

**Het encoderen is bewezen. Het afspelen niet.**

Dat de metadata er correct in zit is te controleren:

```bash
mediainfo resultaat.mkv | grep -i "HDR format"
ffprobe -v quiet -select_streams v:0 -show_frames -read_intervals "%+#1" \
        -show_entries frame=side_data_list resultaat.mkv | grep -i dv_profile
```

Maar **AV1 met Dolby Vision profiel 10 speelt nergens gedocumenteerd af.** Niet
in Jellyfin, niet in Infuse, niet op een Apple TV, niet op de meeste TV's. Het
formaat bestaat en de metadata is er, maar de spelerskant is er nog niet.

Dat is geen fout in dit project en niets wat je kunt oplossen. Het is de reden
dat `dv_modus` standaard op `skip` staat.

## Hoe je het uitzoekt zonder risico

1. `ffmpeg -h encoder=libsvtav1 | grep -i dolby` moet de optie tonen
2. Maak een **kopie** van één profiel 8.1-film in je testmap
3. Zet `dv_modus` op `av1` in het profiel van alleen die testlibrary
4. Laat die ene film verwerken
5. `mediainfo resultaat.mkv | grep -i "HDR format"` — er moet Dolby Vision
   staan. Zo niet: stoppen, `dv_modus` terug op `skip`
6. **Zet het bestand op je TV en kijk ernaar.** Niet naar de metadata kijken —
   werkelijk kijken, een paar minuten

Werkt stap 6 niet, dan zet je `dv_modus` terug op `skip` en verlies je niets:
je originelen staan er nog, want de pipeline vervangt pas na verificatie.

## Wat je ondertussen wel wint

Je DV-films zijn je grootste bestanden, dus het is verleidelijk om daar te
beginnen. Doe dat niet. De rest van je bibliotheek — SDR 1080p en HDR10 — is
verreweg het grootste deel van je 50 TB, en die tak is wél bewezen van begin
tot eind, tot en met het afspelen.

Laat `dv_modus` op `skip` staan tot de rest maanden zonder verrassingen heeft
gedraaid. Dan is de spelerskant misschien ook verder.

## Verificatie in de pipeline

Bij een DV-bestand controleert de verificatie (fase 3) of `dv_profile` in het
resultaat nog gevuld is. Is dat niet zo, dan is de job **mislukt** en blijft je
origineel onaangeraakt. Zie `spec/04-veiligheidsregels.md`, regel 3.

Dat vangnet is de reden dat je dit veilig kunt uitproberen.
