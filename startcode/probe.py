#!/usr/bin/env python3
"""Uitlezen wat er in een mediabestand zit.

Dit is de belangrijkste module van het hele project en de enige die je beter
niet zelf opnieuw verzint. De reden staat in spec/02-domeinkennis.md, maar kort:
HDR10-metadata zit meestal NIET waar je hem zoekt.

Deze code is getest tegen echte HDR10-bestanden en levert alle tien de
mastering-displaywaarden correct terug. Overgenomen uit een draaiende
Unmanic-plugin, losgemaakt van Unmanic.

Zelfstandig te draaien:
    python3 probe.py /pad/naar/film.mkv
"""
import json
import logging
import subprocess

logger = logging.getLogger(__name__)


def _ffprobe_json(args, timeout=300):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
        logger.warning("ffprobe faalde: %s", exc)
        return None


def _breuk(waarde):
    """ffprobe geeft coordinaten als breuk: '13250/50000'."""
    tekst = str(waarde)
    if "/" in tekst:
        teller, _, noemer = tekst.partition("/")
        try:
            n = float(noemer)
            return float(teller) / n if n else 0.0
        except ValueError:
            return 0.0
    try:
        return float(tekst)
    except ValueError:
        return 0.0


def _kbps_uit_pakketten(pad, duur):
    """Meet de videobitrate door pakketgroottes te tellen.

    In Matroska staat bijna nooit een bitrate per spoor. Terugvallen op
    "bestandsgrootte gedeeld door speelduur" telt de audio mee, en bij een
    UHD-remux is dat zomaar 15 Mbit/s aan TrueHD en DTS-HD MA. Dan komt een
    bestand boven je drempel uit op zijn audio in plaats van op zijn beeld.

    We lezen daarom een venster uit het MIDDEN van het bestand. Aan het begin
    staan logo's en zwart beeld, en die comprimeren zo goed dat je de film
    zou onderschatten.
    """
    if duur <= 0:
        return 0
    start = max(0.0, duur * 0.4)
    venster = min(60.0, max(10.0, duur * 0.1))

    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-read_intervals", "{:.0f}%+{:.0f}".format(start, venster),
             "-show_entries", "packet=size,duration_time",
             "-of", "compact=p=0:nk=0", pad],
            capture_output=True, text=True, timeout=300,
        )
    except (subprocess.SubprocessError, OSError):
        return 0
    if r.returncode != 0 or not r.stdout:
        return 0

    bytes_totaal, tijd_totaal = 0, 0.0
    for regel in r.stdout.splitlines():
        for veld in regel.split("|"):
            sleutel, _, waarde = veld.partition("=")
            if sleutel == "size":
                try:
                    bytes_totaal += int(waarde)
                except ValueError:
                    pass
            elif sleutel == "duration_time":
                try:
                    tijd_totaal += float(waarde)
                except ValueError:
                    pass

    if tijd_totaal <= 1.0:
        tijd_totaal = venster
    if bytes_totaal <= 0 or tijd_totaal <= 0:
        return 0
    return round(bytes_totaal * 8 / tijd_totaal / 1000)


def video_kbps(videospoor, alle_streams, fmt, duur, pad):
    """Bitrate van het VIDEOSPOOR. Vier methodes, in volgorde van betrouwbaarheid."""
    direct = int(videospoor.get("bit_rate") or 0)
    if direct > 0:
        return round(direct / 1000)

    # mkvmerge schrijft dit als tag; ffmpeg doet het niet.
    tags = videospoor.get("tags") or {}
    for sleutel in ("BPS", "BPS-eng", "BPS-en", "bps"):
        try:
            waarde = int(tags.get(sleutel) or 0)
            if waarde > 0:
                return round(waarde / 1000)
        except (TypeError, ValueError):
            pass

    gemeten = _kbps_uit_pakketten(pad, duur)
    if gemeten > 0:
        return gemeten

    if duur <= 0:
        return 0
    try:
        totaal_bits = int(fmt.get("size") or 0) * 8
    except (TypeError, ValueError):
        return 0
    if not totaal_bits:
        return 0

    audio_bits = 0
    for s in alle_streams:
        if s.get("codec_type") != "audio":
            continue
        br = int(s.get("bit_rate") or 0)
        if not br:
            a_tags = s.get("tags") or {}
            for sleutel in ("BPS", "BPS-eng", "BPS-en", "bps"):
                try:
                    br = int(a_tags.get(sleutel) or 0)
                except (TypeError, ValueError):
                    br = 0
                if br:
                    break
        audio_bits += br * duur

    return round(max(0.0, totaal_bits - audio_bits) / duur / 1000)


def probe(pad):
    """Volledige analyse van een bestand. Geeft None als het niet leesbaar is.

    Retourneert een dict met onder meer:
        codec, width, height, fps, duration, size, kbps
        is_hdr10, mastering_display, md_ruw, content_light
        hdr10plus, dv_profile, dv_compat
        audio[], subs[], attachments
    """
    data = _ffprobe_json(["-show_streams", "-show_format", pad])
    if not data or not data.get("streams"):
        return None

    alle = data["streams"]
    videos = [x for x in alle if x.get("codec_type") == "video"
              and not (x.get("disposition") or {}).get("attached_pic")]
    if not videos:
        return None

    spoor = videos[0]
    fmt = data.get("format", {})
    duur = float(fmt.get("duration") or 0)

    info = {
        "pad": pad,
        "codec": spoor.get("codec_name", ""),
        "width": int(spoor.get("width") or 0),
        "height": int(spoor.get("height") or 0),
        "fps": spoor.get("r_frame_rate", "24000/1001"),
        "duration": duur,
        "size": int(fmt.get("size") or 0),
        "container": (fmt.get("format_name") or ""),
    }
    info["kbps"] = video_kbps(spoor, alle, fmt, duur, pad)

    # ── HIER ZIT DE TRUC ──────────────────────────────────────────────
    # Mastering display wordt als SEI in de bitstream meegedragen en komt
    # dus in FRAME-side-data terecht, niet in stream-side-data. Wie alleen
    # -show_streams leest ziet HDR10 op de meeste bestanden niet, encodeert
    # het er stilzwijgend uit, en merkt dat pas op zijn TV.
    side_data = list(spoor.get("side_data_list") or [])

    frames = _ffprobe_json([
        "-select_streams", "v:0", "-read_intervals", "%+#120",
        "-show_frames", "-show_entries", "frame=side_data_list", pad,
    ])
    info["hdr10plus"] = False
    if frames:
        info["hdr10plus"] = "2094-40" in json.dumps(frames)
        aanwezig = {x.get("side_data_type") for x in side_data}
        for frame in frames.get("frames", []):
            for item in (frame.get("side_data_list") or []):
                if item.get("side_data_type") not in aanwezig:
                    side_data.append(item)
                    aanwezig.add(item.get("side_data_type"))

    md = next((x for x in side_data
               if x.get("side_data_type") == "Mastering display metadata"), None)
    cll = next((x for x in side_data
                if x.get("side_data_type") == "Content light level metadata"), None)

    # Formaat voor SVT-AV1: G, B, R, WP, L - in die volgorde.
    info["mastering_display"] = ""
    if md:
        info["mastering_display"] = (
            "G({:.4f},{:.4f})".format(_breuk(md.get("green_x")), _breuk(md.get("green_y")))
            + "B({:.4f},{:.4f})".format(_breuk(md.get("blue_x")), _breuk(md.get("blue_y")))
            + "R({:.4f},{:.4f})".format(_breuk(md.get("red_x")), _breuk(md.get("red_y")))
            + "WP({:.4f},{:.4f})".format(_breuk(md.get("white_point_x")),
                                         _breuk(md.get("white_point_y")))
            + "L({:.4f},{:.4f})".format(_breuk(md.get("max_luminance")),
                                        _breuk(md.get("min_luminance")))
        )
    info["is_hdr10"] = bool(info["mastering_display"])

    # Ruwe waarden apart: mkvmerge wil ROOD-GROEN-BLAUW, SVT-AV1 wil
    # GROEN-BLAUW-ROOD. Die volgorde omdraaien is een klassieke stille fout.
    info["md_ruw"] = {}
    if md:
        for k in ("red_x", "red_y", "green_x", "green_y", "blue_x", "blue_y",
                  "white_point_x", "white_point_y", "max_luminance", "min_luminance"):
            info["md_ruw"][k] = _breuk(md.get(k))

    info["content_light"] = ("{},{}".format(cll.get("max_content"), cll.get("max_average"))
                             if cll else "")

    dv = next((x for x in side_data if x.get("dv_profile") is not None), None)
    info["dv_profile"] = int(dv.get("dv_profile")) if dv else 0
    info["dv_compat"] = int(dv.get("dv_bl_signal_compatibility_id") or 0) if dv else 0

    info["audio"] = [
        {"index": int(x["index"]),
         "codec": x.get("codec_name", ""),
         "kanalen": int(x.get("channels") or 0),
         "taal": str((x.get("tags") or {}).get("language") or "und").lower(),
         "titel": str((x.get("tags") or {}).get("title") or ""),
         "commentaar": bool((x.get("disposition") or {}).get("comment")),
         "bit_rate": int(x.get("bit_rate") or 0)}
        for x in alle if x.get("codec_type") == "audio"
    ]
    info["subs"] = [
        {"index": int(x["index"]),
         "codec": str(x.get("codec_name") or "").lower(),
         "taal": str((x.get("tags") or {}).get("language") or "und").lower(),
         "forced": bool((x.get("disposition") or {}).get("forced")),
         "commentaar": bool((x.get("disposition") or {}).get("comment"))}
        for x in alle if x.get("codec_type") == "subtitle"
    ]
    info["attachments"] = len([x for x in alle if x.get("codec_type") == "attachment"])

    return info


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("Gebruik: python3 probe.py <bestand>")
    uitslag = probe(sys.argv[1])
    if not uitslag:
        sys.exit("Niet te lezen: {}".format(sys.argv[1]))
    print(json.dumps(uitslag, indent=2, ensure_ascii=False))
