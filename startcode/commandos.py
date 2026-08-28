#!/usr/bin/env python3
"""Bouwt de ffmpeg- en mkvmerge-commando's.

HandBrake komt hier bewust niet meer in voor. Sinds ffmpeg 7.1 schrijft
libsvtav1 de Dolby Vision-RPU zelf weg met "-dolbyvision 1", en dat is
empirisch geverifieerd: een echt profiel 8.1-bestand komt er als AV1 met
DV profiel 10 uit, met de HDR10-laag, de audio, de ondertitels en de
lettertypen intact.

Dat scheelt een bouwstap van veertig minuten, een OS-afhankelijkheid, en
een encoder die geen "-progress pipe:1" kent.

Elke functie geeft een LIJST terug, nooit een string. Nooit shell=True: je
bestandsnamen bevatten spaties, haakjes, apostroffen en accenten, en een
bibliotheek van 50 TB vindt gegarandeerd de naam die je quoting breekt.

Alle commando's schrijven MKV. Verander dat niet: verandert de extensie, dan
breken je .nfo-bestanden, je externe .srt en je Radarr-koppeling.
"""
import os

# Ondertitelformaten die Matroska aankan. Alles daarbuiten (mov_text uit MP4)
# moet omgezet, anders faalt de mux met "Subtitle codec 94213 is not supported".
MKV_SUB_CODECS = ("subrip", "srt", "ass", "ssa", "webvtt",
                  "dvd_subtitle", "hdmv_pgs_subtitle", "dvb_subtitle")


def _basis(bron):
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats",
            "-progress", "pipe:1", "-y", "-i", bron]


def _maps(besluit):
    """De -map-vlaggen: welke sporen gaan mee."""
    args = ["-map", "0:v:0"]
    for idx in besluit.audio_houden:
        args += ["-map", "0:{}".format(idx)]
    for idx in besluit.subs_houden:
        args += ["-map", "0:{}".format(idx)]
    if besluit.attachments_houden:
        # 0:t? = alle attachments; het vraagteken voorkomt een fout als er
        # toch geen zijn. Dit zijn de lettertypen van je ASS-ondertitels.
        args += ["-map", "0:t?"]
    return args


def _sub_codecs(besluit, info):
    """Per bewaard ondertitelspoor: past het in MKV, of moet het om?

    LET OP DE VOLGORDE waarin je dit in het commando zet. Deze vlaggen moeten
    NA "-c copy" komen, anders overschrijft die generieke vlag ze weer en
    faalt de mux alsnog. ffmpeg waarschuwt daar wel voor, maar in een log van
    duizend regels leest niemand dat:

        Multiple -c options specified for stream 2, only the last will be used
    """
    args = []
    op_index = {s["index"]: s for s in info["subs"]}
    for positie, idx in enumerate(besluit.subs_houden):
        codec = op_index.get(idx, {}).get("codec", "")
        if codec and codec not in MKV_SUB_CODECS:
            args += ["-c:s:{}".format(positie), "srt"]
    return args


def opschonen(info, besluit, bron, doel):
    """Alleen sporen uitdunnen en remuxen naar MKV. Raakt de video niet aan.

    Dit is de grootste snelle winst van het hele project. Een UHD-remux met
    TrueHD Atmos, DTS-HD MA en een commentaarspoor draagt makkelijk 15 tot
    20 GB aan audio mee. Kost minuten per bestand, geen kwaliteitsverlies.
    """
    return (_basis(bron) + _maps(besluit)
            + ["-c", "copy"] + _sub_codecs(besluit, info)
            + ["-map_chapters", "0", "-max_muxing_queue_size", "9999", doel])


def encode_cpu(info, besluit, inst, bron, doel):
    """SVT-AV1 op de CPU. De enige route die HDR10 correct meedraagt."""
    kl = "2160" if info["height"] > 1200 else "1080"
    crf = inst["crf_" + kl]
    preset = inst["preset_" + kl]

    params = ["film-grain={}".format(inst["film_grain"])]
    kleur = []
    dv = []
    if getattr(besluit, "dolby_vision", False):
        # Sinds ffmpeg 7.1 schrijft libsvtav1 de Dolby Vision-RPU zelf weg.
        #
        # DIT MOET EXPLICIET OP 1. De standaardstand is "auto", en die
        # schrijft de RPU NIET weg - empirisch vastgesteld op een echt
        # profiel 8.1-bestand: met "auto" komt er dv_profile=0 uit, met
        # "-dolbyvision 1" komt er dv_profile=10 uit. Geen waarschuwing,
        # geen foutmelding. Je merkt het pas op je TV.
        dv = ["-dolbyvision", "1"]
    if besluit.behoud_hdr and info["is_hdr10"]:
        # Er bestaat GEEN ffmpeg-optie -master_display. Voor SVT-AV1 gaat het
        # via -svtav1-params, met decimale coordinaten en luminantie in nits.
        params.append("mastering-display=" + info["mastering_display"])
        if info["content_light"]:
            params.append("content-light=" + info["content_light"])
        kleur = ["-color_primaries", "bt2020",
                 "-color_trc", "smpte2084",
                 "-colorspace", "bt2020nc"]

    return (_basis(bron) + _maps(besluit)
            + ["-c", "copy"] + _sub_codecs(besluit, info)
            + ["-c:v", "libsvtav1",
               "-crf", str(crf), "-preset", str(preset),
               "-pix_fmt", "yuv420p10le",
               "-svtav1-params", ":".join(params)]
            + dv + kleur
            + ["-map_chapters", "0", "-max_muxing_queue_size", "9999", doel])


def encode_gpu(info, besluit, inst, bron, doel, render_node="/dev/dri/renderD128"):
    """av1_qsv op de Intel Arc. Twintig keer sneller, geen HDR-metadata.

    p010le, niet yuv420p10le: QSV wil zijn eigen 10-bits formaat. Geef je het
    verkeerde mee, dan valt ffmpeg terug op 8-bit of faalt de encode.
    """
    return (["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats",
             "-progress", "pipe:1", "-y",
             "-init_hw_device", "qsv=hw,child_device={}".format(render_node),
             "-filter_hw_device", "hw",
             "-i", bron]
            + _maps(besluit)
            + ["-c", "copy"] + _sub_codecs(besluit, info)
            + [# Expliciet uploaden naar de GPU. Zonder deze filter valt
               # ffmpeg bij sommige bronformaten terug op software.
               "-vf", "format=p010le,hwupload=extra_hw_frames=64",
               "-c:v", "av1_qsv",
               "-global_quality", str(inst["gpu_quality"]),
               "-preset", "veryslow",
               "-map_chapters", "0",
               "-max_muxing_queue_size", "9999", doel])


def dv7_naar_81(bron, fps, werkmap):
    """Drie commando's die profiel 7 (dual layer) naar 8.1 omzetten.

    Alleen nodig bij profiel 7. ffmpeg kan de enhancement layer niet aan,
    dus die moet er met dovi_tool af voordat je kunt encoderen. Profiel 8.1
    gaat rechtstreeks door libsvtav1.

    Geeft (lijst_van_commandos, resultaatpad) terug. De aanroeper MOET de
    werkmap opruimen: de ruwe HEVC-stroom van een UHD-bron is tientallen GB.
    """
    raw = os.path.join(werkmap, "in.hevc")
    bl81 = os.path.join(werkmap, "bl81.hevc")
    uit = os.path.join(werkmap, "p81.mkv")
    return ([
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", bron,
         "-map", "0:v:0", "-c", "copy", "-bsf:v", "hevc_mp4toannexb",
         "-f", "hevc", raw],
        ["dovi_tool", "-m", "2", "convert", "--discard", "-i", raw, "-o", bl81],
        # --default-duration is verplicht: ruwe HEVC heeft geen timing, en
        # zonder deze vlag loopt je audio uit de pas.
        ["mkvmerge", "-o", uit, "-D", bron,
         "--default-duration", "0:{}p".format(fps), bl81,
         "--track-order", "1:0"],
    ], uit)


def herstel_hdr(md_ruw, content_light, bestand, doel):
    """Zet HDR10-metadata op containerniveau terug met mkvmerge.

    Nodig na een GPU-encode, want av1_qsv kan mastering display niet
    meedragen. Matroska kan die metadata zelf dragen en ffprobe leest hem
    daarna gewoon weer uit. Getest: alle tien de waarden komen exact terug.

    LET OP DE VOLGORDE. mkvmerge wil ROOD, GROEN, BLAUW. SVT-AV1 en x265
    gebruiken GROEN, BLAUW, ROOD. Verwissel je die, dan krijg je een bestand
    dat er in de metadata correct uitziet maar verkeerde kleuren claimt.
    """
    args = ["mkvmerge", "-q", "-o", doel,
            "--colour-primaries", "0:9",
            "--colour-transfer-characteristics", "0:16",
            "--colour-matrix-coefficients", "0:9",
            "--chromaticity-coordinates",
            "0:{:.4f},{:.4f},{:.4f},{:.4f},{:.4f},{:.4f}".format(
                md_ruw["red_x"], md_ruw["red_y"],
                md_ruw["green_x"], md_ruw["green_y"],
                md_ruw["blue_x"], md_ruw["blue_y"]),
            "--white-colour-coordinates", "0:{:.4f},{:.4f}".format(
                md_ruw["white_point_x"], md_ruw["white_point_y"]),
            "--max-luminance", "0:{:.4f}".format(md_ruw["max_luminance"]),
            "--min-luminance", "0:{:.4f}".format(md_ruw["min_luminance"])]
    if content_light:
        maxc, _, maxa = str(content_light).partition(",")
        args += ["--max-content-light", "0:" + maxc,
                 "--max-frame-light", "0:" + maxa]
    args.append(bestand)
    return args
