#!/usr/bin/env bash
# Maakt korte testbestanden die elk een valkuil uit spec/02-domeinkennis.md
# afdekken. Draait in seconden.
#
#   ./test/maak-testbestanden.sh [doelmap]     (standaard: ./testdata)
#
# WAAROM DIT HET BELANGRIJKSTE SCRIPT VAN HET PROJECT IS
#
# Je feedbackloop is een encode. Test je op echte films, dan kost elke
# bugronde uren en ben je na drie fixes een avond kwijt. Met bestanden van
# zes seconden is diezelfde ronde twintig seconden. Alle eigenschappen die
# ertoe doen - mastering display, ASS met lettertype, mov_text, lage bitrate -
# zitten er gewoon in.
#
# Draai je echte bibliotheek pas als de tests groen zijn.
set -euo pipefail

DOEL="${1:-./testdata}"
mkdir -p "$DOEL"
echo "Testbestanden in $DOEL"

# ── 1. HDR10 met mastering display in FRAME-side-data ─────────────────
# Dit is het bestand waarop de helft van alle pipelines stukgaat: wie alleen
# -show_streams leest ziet hier geen HDR en encodeert het er stil uit.
echo "  hdr10.mkv           HDR10, mastering display + content light"
ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i testsrc2=size=1920x1080:rate=24 \
    -f lavfi -i "sine=frequency=440" -t 6 \
    -c:v libx265 -pix_fmt yuv420p10le -preset ultrafast -crf 16 \
    -x265-params "hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400" \
    -c:a aac -metadata:s:a:0 language=eng \
    "$DOEL/hdr10.mkv"

# ── 2. SDR 1080p, meerdere audiotalen, commentaarspoor ────────────────
echo "  sdr-meertalig.mkv   3 audiosporen waarvan 1 commentaar"
ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i testsrc2=size=1920x1080:rate=24 \
    -f lavfi -i "sine=frequency=440" \
    -f lavfi -i "sine=frequency=880" \
    -f lavfi -i "sine=frequency=220" -t 6 \
    -map 0:v -map 1:a -map 2:a -map 3:a \
    -c:v libx264 -preset ultrafast -c:a aac \
    -metadata:s:a:0 language=eng \
    -metadata:s:a:1 language=nld \
    -metadata:s:a:2 language=eng -metadata:s:a:2 title="Director Commentary" \
    -disposition:a:2 comment \
    "$DOEL/sdr-meertalig.mkv"

# ── 3. Anime: ASS-ondertitel met ingesloten lettertype ────────────────
# Gooi je die attachment weg, dan valt de speler terug op een standaard-
# lettertype en is de hele typografie weg.
echo "  anime-ass.mkv       ASS-ondertitel + lettertype als attachment"
printf '[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name\nStyle: Default\n\n[Events]\nFormat: Layer, Start, End, Style, Text\nDialogue: 0,0:00:01.00,0:00:03.00,Default,Test\n' > "$DOEL/.sub.ass"
FONT=$(find /usr/share/fonts /etc/alternatives -name "*.ttf" -print -quit 2>/dev/null)
if [[ -z "$FONT" ]]; then
    echo "     LET OP: geen .ttf gevonden, attachment overgeslagen"
    ffmpeg -hide_banner -loglevel error -y \
        -f lavfi -i testsrc2=size=1920x1080:rate=24 \
        -f lavfi -i "sine=frequency=440" -i "$DOEL/.sub.ass" -t 6 \
        -map 0:v -map 1:a -map 2:s -c:v libx264 -preset ultrafast -c:a aac -c:s copy \
        -metadata:s:a:0 language=jpn -metadata:s:s:0 language=eng \
        "$DOEL/anime-ass.mkv"
else
    ffmpeg -hide_banner -loglevel error -y \
        -f lavfi -i testsrc2=size=1920x1080:rate=24 \
        -f lavfi -i "sine=frequency=440" -i "$DOEL/.sub.ass" -t 6 \
        -map 0:v -map 1:a -map 2:s -c:v libx264 -preset ultrafast -c:a aac -c:s copy \
        -metadata:s:a:0 language=jpn -metadata:s:s:0 language=eng \
        -attach "$FONT" -metadata:s:t:0 mimetype=application/x-truetype-font \
        "$DOEL/anime-ass.mkv"
fi

# ── 4. MP4 met mov_text ───────────────────────────────────────────────
# mov_text past NIET in Matroska. Zet je dit niet om, dan faalt de mux met
# "Subtitle codec 94213 is not supported".
echo "  mp4-movtext.mp4     mov_text-ondertitel, moet omgezet worden"
ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i testsrc2=size=1280x720:rate=24 \
    -f lavfi -i "sine=frequency=440" -t 6 \
    -c:v libx264 -preset ultrafast -c:a aac -metadata:s:a:0 language=eng \
    "$DOEL/.tmp.mp4"
ffmpeg -hide_banner -loglevel error -y -i "$DOEL/.tmp.mp4" -i "$DOEL/.sub.ass" \
    -map 0 -map 1 -c copy -c:s mov_text -metadata:s:s:0 language=eng \
    "$DOEL/mp4-movtext.mp4"

# ── 5. Lage bitrate: moet ONGEMOEID blijven ───────────────────────────
# Dit bestand vergeet iedereen, en het is precies waar een pipeline stukgaat.
echo "  lage-bitrate.mkv    moet worden overgeslagen, niet hercodeerd"
ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i testsrc2=size=1920x1080:rate=24 \
    -f lavfi -i "sine=frequency=440" -t 6 \
    -c:v libx264 -preset ultrafast -b:v 500k -c:a aac \
    -metadata:s:a:0 language=eng \
    "$DOEL/lage-bitrate.mkv"

# ── 5b. Echte hardlinkset: zelfde inode, twee bibliotheeknamen ───────
# De scanner moet beide namen als hardlink herkennen en standaard weigeren.
echo "  hardlinks/          twee namen voor exact dezelfde inode"
mkdir -p "$DOEL/hardlinks"
cp "$DOEL/sdr-meertalig.mkv" "$DOEL/hardlinks/seed-bestand.mkv"
ln -f "$DOEL/hardlinks/seed-bestand.mkv" "$DOEL/hardlinks/jellyfin-bestand.mkv"

# ── 6. Al AV1: moet worden overgeslagen ───────────────────────────────
echo "  al-av1.mkv          al AV1, moet worden overgeslagen"
ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i testsrc2=size=1280x720:rate=24 \
    -f lavfi -i "sine=frequency=440" -t 6 \
    -c:v libsvtav1 -crf 50 -preset 12 -svtav1-params "lp=1" -c:a aac \
    -metadata:s:a:0 language=eng \
    "$DOEL/al-av1.mkv"

# ── 7. Dolby Vision profiel 8.1 ───────────────────────────────────────
# Alleen als dovi_tool aanwezig is. Een echt DV-bestand maken kan: dovi_tool
# genereert een synthetische RPU en injecteert die in een HEVC-stroom.
if command -v dovi_tool >/dev/null 2>&1 && command -v mkvmerge >/dev/null 2>&1; then
    echo "  dv81.mkv            Dolby Vision profiel 8.1 met HDR10-laag"
    cat > "$DOEL/.dv.json" <<'JSON'
{
  "cm_version": "V29",
  "profile": "8.1",
  "length": 144,
  "level6": { "max_display_mastering_luminance": 1000,
              "min_display_mastering_luminance": 1,
              "max_content_light_level": 1000,
              "max_frame_average_light_level": 400 }
}
JSON
    dovi_tool generate -j "$DOEL/.dv.json" -o "$DOEL/.rpu.bin" >/dev/null 2>&1
    ffmpeg -hide_banner -loglevel error -y -i "$DOEL/hdr10.mkv" \
        -map 0:v:0 -c copy -bsf:v hevc_mp4toannexb -f hevc "$DOEL/.raw.hevc"
    dovi_tool inject-rpu -i "$DOEL/.raw.hevc" --rpu-in "$DOEL/.rpu.bin" \
        -o "$DOEL/.dv81.hevc" >/dev/null 2>&1
    mkvmerge -q -o "$DOEL/dv81.mkv" --default-duration 0:24p "$DOEL/.dv81.hevc" \
        --no-video "$DOEL/anime-ass.mkv" >/dev/null 2>&1
    rm -f "$DOEL/.dv.json" "$DOEL/.rpu.bin" "$DOEL/.raw.hevc" "$DOEL/.dv81.hevc"
else
    echo "  dv81.mkv            OVERGESLAGEN (dovi_tool of mkvmerge ontbreekt)"
fi

# ── 8. Mapstructuur voor de bestandsfilter ────────────────────────────
echo "  mappen/             valkuilen voor de naamfilter"
mkdir -p "$DOEL/mappen/Dune Part Two (2024)" \
         "$DOEL/mappen/Trailer Park Boys/Season 01" \
         "$DOEL/mappen/Short Term 12 (2013)" \
         "$DOEL/mappen/Film Met Extras (2021)/Extras"
cp "$DOEL/lage-bitrate.mkv" "$DOEL/mappen/Dune Part Two (2024)/Dune.Part.Two.2024.2160p.mkv"
head -c 200000 "$DOEL/lage-bitrate.mkv" > "$DOEL/mappen/Dune Part Two (2024)/Dune Part Two (2024)-trailer.mkv"
cp "$DOEL/lage-bitrate.mkv" "$DOEL/mappen/Trailer Park Boys/Season 01/Trailer.Park.Boys.S01E01.1080p.mkv"
cp "$DOEL/lage-bitrate.mkv" "$DOEL/mappen/Short Term 12 (2013)/Short.Term.12.2013.1080p.BluRay.x264.mkv"
cp "$DOEL/lage-bitrate.mkv" "$DOEL/mappen/Film Met Extras (2021)/Film Met Extras (2021).mkv"
head -c 100000 "$DOEL/lage-bitrate.mkv" > "$DOEL/mappen/Film Met Extras (2021)/Extras/Making Of.mkv"

rm -f "$DOEL/.sub.ass" "$DOEL/.tmp.mp4"

# Vangnet: testbestanden horen enkele megabytes te zijn. Is er een groot,
# dan stond er ergens een "-t" voor een "-i" en geldt hij als input-optie
# in plaats van als lengte van de uitvoer.
GROOT=$(find "$DOEL" -maxdepth 1 -type f -size +50M -print -quit)
if [[ -n "$GROOT" ]]; then
    echo
    echo "LET OP: deze testbestanden zijn veel te groot:"
    echo "$GROOT" | sed 's/^/  /'
    echo "Controleer dat elke -t NA alle -i staat."
fi

echo
echo "Klaar. $(find "$DOEL" -name '*.mkv' -o -name '*.mp4' | wc -l) bestanden."
echo "Draai nu:  python3 -m pytest test/ -v"
