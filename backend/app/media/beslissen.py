#!/usr/bin/env python3
"""Beslissen wat er met een bestand moet gebeuren.

Deze module doet NIETS. Hij kijkt naar de uitslag van probe.py en geeft een
Besluit terug. Dat is met opzet: in de pipeline wordt eerst voor de hele
bibliotheek besloten, dan bewaard, dan door jou bekeken, en pas daarna
uitgevoerd. Zie spec/04-veiligheidsregels.md, regel 1.

Zelfstandig te draaien:
    python3 beslissen.py /pad/naar/film.mkv
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Standaardinstellingen. In de pipeline komen die per library uit de database.
STANDAARD: dict[str, Any] = {
    "drempel_2160": 20000,
    "drempel_1080": 8000,
    "drempel_720": 4000,
    "crf_2160": 27,
    "crf_1080": 29,
    "crf_720": 31,
    # 0 = kwaliteitsgestuurd met CRF. Een waarde > 0 is een gemiddelde
    # doelbitrate in kbps; de encoder krijgt daarnaast een ruime VBV-band.
    # 6500 kbps is een bruikbaar startpunt voor 4K-DV op deze host.
    "doel_bitrate_2160_kbps": 6500,
    "doel_bitrate_1080_kbps": 0,
    "doel_bitrate_720_kbps": 0,
    "preset_2160": 8,
    "preset_1080": 6,
    "preset_720": 6,
    # 0 = automatisch. De uitvoerder kiest dan op basis van beschikbaar RAM
    # een veilige SVT-paralleliteit; een expliciete waarde blijft begrensd.
    "svt_threads_2160": 0,
    "svt_threads_1080": 0,
    "svt_threads_720": 0,
    "gpu_quality": 28,
    # Alleen de begrensde jobretry mag dit tijdelijk verlagen. Het profiel
    # houdt 64 voor normale QSV-doorvoer.
    "gpu_extra_hw_frames": 64,
    "gpu_voor_sdr_1080": True,
    "gpu_voor_sdr_4k": False,
    "gpu_voor_hdr10": False,
    "dv_modus": "skip",  # skip | av1
    "film_grain": 0,
    "audio_talen": ["nld", "eng", "jpn"],
    "ondertitel_talen": ["nld", "eng"],
    "commentaar_weg": True,
    "beste_per_taal": True,
}


class Besluit(BaseModel):
    """Wat er met een bestand gaat gebeuren, en waarom."""

    model_config = ConfigDict(extra="forbid")

    actie: str
    baan: str = ""
    encoder: str = ""
    reden: str = ""
    opschonen: bool = False
    behoud_hdr: bool = False
    herstel_hdr_na: bool = False
    dolby_vision: bool = False
    dv7_omzetten: bool = False
    audio_houden: list[int] = Field(default_factory=list)
    subs_houden: list[int] = Field(default_factory=list)
    attachments_houden: bool = False
    waarschuwingen: list[str] = Field(default_factory=list)


def klasse_van(hoogte: int) -> str:
    if hoogte > 1200:
        return "2160"
    if hoogte > 800:
        return "1080"
    return "720"


# Ondertitelformaten die Matroska rechtstreeks aankan. mov_text uit MP4 staat
# hier bewust niet bij: dat past niet in MKV en moet omgezet worden.
MKV_SUB_CODECS = (
    "subrip",
    "srt",
    "ass",
    "ssa",
    "webvtt",
    "dvd_subtitle",
    "hdmv_pgs_subtitle",
    "dvb_subtitle",
)
ASS_CODECS = ("ass", "ssa")


def kies_sporen(
    info: dict[str, Any], inst: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, bool]:
    """Welke audio- en ondertitelsporen blijven over."""
    audio_talen = inst["audio_talen"]
    sub_talen = inst["ondertitel_talen"]

    houden = [
        a
        for a in info["audio"]
        if a["taal"] in audio_talen and not (inst["commentaar_weg"] and _is_commentaar(a))
    ]

    if inst["beste_per_taal"] and houden:
        per_taal: dict[str, dict[str, Any]] = {}
        for a in houden:
            huidig = per_taal.get(a["taal"])
            if (
                huidig is None
                or a["kanalen"] > huidig["kanalen"]
                or (a["kanalen"] == huidig["kanalen"] and a["bit_rate"] > huidig["bit_rate"])
            ):
                per_taal[a["taal"]] = a
        houden = list(per_taal.values())

    # VEILIGHEIDSNET: nooit een bestand zonder audio opleveren. Een Koreaanse
    # film waarvan geen enkel spoor aan je talenlijst voldoet houdt zijn
    # originele spoor.
    noodgreep = False
    if not houden and info["audio"]:
        houden = [info["audio"][0]]
        noodgreep = True

    subs = info["subs"]
    subs_houden = (
        subs
        if not sub_talen
        else [
            s
            for s in subs
            if s["taal"] in sub_talen and not (inst["commentaar_weg"] and s["commentaar"])
        ]
    )

    # Blijft er een ASS/SSA-spoor over, dan MOETEN de lettertypen mee.
    # Anime-releases hangen hun typografie als attachment in het bestand.
    attachments = any(s["codec"] in ASS_CODECS for s in subs_houden) and info["attachments"] > 0

    return houden, subs_houden, attachments, noodgreep


def _is_commentaar(spoor: dict[str, Any]) -> bool:
    titel = spoor.get("titel", "").lower()
    return spoor.get("commentaar") or "commentary" in titel or "commentaar" in titel


def beslis(info: dict[str, Any], inst: dict[str, Any] | None = None) -> Besluit:
    """De hele beslisboom. Zie spec/02-domeinkennis.md voor het waarom."""
    inst = {**STANDAARD, **(inst or {})}
    b = Besluit(actie="overslaan")

    audio_houden, subs_houden, attachments, noodgreep = kies_sporen(info, inst)
    b.audio_houden = [a["index"] for a in audio_houden]
    b.subs_houden = [s["index"] for s in subs_houden]
    b.attachments_houden = attachments
    if noodgreep:
        b.waarschuwingen.append("geen enkel audiospoor matchte de talen; eerste origineel behouden")

    if info["duration"] <= 0:
        b.reden = "speelduur onbekend"
        return b

    kl = klasse_van(info["height"])
    drempel = inst["drempel_" + kl]

    # ── Dolby Vision ──────────────────────────────────────────────────
    if info["dv_profile"]:
        p = info["dv_profile"]
        if p == 5:
            # Profiel 5 heeft geen HDR10-basislaag. Het beeld staat in
            # IPT-PQ-c2 en er is geen correcte niet-DV-interpretatie van.
            # Haal je de DV eruit, dan krijg je paars-groene onzin.
            b.reden = "DV profiel 5: geen HDR10-terugval, nooit aanraken"
            return b
        if inst["dv_modus"] != "av1":
            b.reden = f"DV profiel {p}: dv_modus staat op skip"
            return b
        if info["kbps"] and info["kbps"] <= drempel:
            b.reden = "{} kbps onder drempel {}".format(info["kbps"], drempel)
            return b

        # Sinds ffmpeg 7.1 schrijft libsvtav1 de RPU zelf weg met
        # "-dolbyvision 1". HandBrake is hier niet meer voor nodig.
        # LET OP: de standaardstand "auto" schrijft de RPU NIET. Zonder die
        # expliciete 1 raak je je Dolby Vision stilzwijgend kwijt.
        b.actie, b.baan, b.encoder = "hercoderen", "cpu", "libsvtav1"
        b.dolby_vision = True
        b.behoud_hdr = info["is_hdr10"]
        b.opschonen = True
        b.reden = f"DV profiel {p} naar AV1 profiel 10"
        if p == 7:
            # Dual layer: ffmpeg kan de enhancement layer niet aan, dus eerst
            # met dovi_tool naar 8.1 (single layer).
            b.dv7_omzetten = True
            b.waarschuwingen.append("profiel 7: eerst dovi_tool naar 8.1, dan pas encoderen")
        return b

    # ── HDR10+ ────────────────────────────────────────────────────────
    if info["hdr10plus"]:
        b.reden = "HDR10+: dynamische metadata overleeft geen encode"
        return b

    # ── Al klaar of te klein ──────────────────────────────────────────
    if info["codec"].lower() == "av1":
        b.actie = "opschonen" if _valt_op_te_schonen(info, b) else "overslaan"
        b.reden = "al AV1" + (", alleen sporen uitdunnen" if b.actie == "opschonen" else "")
        b.baan = "cpu" if b.actie == "opschonen" else ""
        b.opschonen = b.actie == "opschonen"
        return b

    if not info["kbps"]:
        b.reden = "bitrate niet te bepalen"
        return b
    if info["kbps"] <= drempel:
        b.actie = "opschonen" if _valt_op_te_schonen(info, b) else "overslaan"
        b.reden = "{} kbps onder drempel {}".format(info["kbps"], drempel)
        if b.actie == "opschonen":
            b.reden += ", alleen sporen uitdunnen"
            b.baan, b.opschonen = "cpu", True
        return b

    # ── Encoder kiezen ────────────────────────────────────────────────
    if info["is_hdr10"]:
        # av1_qsv heeft GEEN optie voor mastering display. Controleer zelf:
        #   ffmpeg -h encoder=av1_qsv | grep -i master   -> geeft niets.
        # HDR via de GPU levert dus een bestand op dat er als SDR uitziet,
        # zonder foutmelding. Alleen doen als je de metadata achteraf
        # terugzet met mkvmerge.
        if inst["gpu_voor_hdr10"]:
            b.baan, b.encoder = "gpu", "av1_qsv"
            b.herstel_hdr_na = True
            b.waarschuwingen.append("HDR via de GPU: metadata moet na afloop terug met mkvmerge")
        else:
            b.baan, b.encoder, b.behoud_hdr = "cpu", "libsvtav1", True
    elif kl == "2160":
        gpu = inst["gpu_voor_sdr_4k"]
        b.baan, b.encoder = ("gpu", "av1_qsv") if gpu else ("cpu", "libsvtav1")
    else:
        gpu = inst["gpu_voor_sdr_1080"]
        b.baan, b.encoder = ("gpu", "av1_qsv") if gpu else ("cpu", "libsvtav1")

    b.actie = "hercoderen"
    b.opschonen = True
    b.behoud_hdr = info["is_hdr10"] and b.encoder == "libsvtav1"
    b.reden = "{}p @ {} kbps naar AV1 via {}".format(
        kl, info["kbps"], "GPU" if b.baan == "gpu" else "CPU"
    )
    return b


def _valt_op_te_schonen(info: dict[str, Any], b: Besluit) -> bool:
    """Is er nog winst te halen zonder de video aan te raken?"""
    return (
        len(b.audio_houden) < len(info["audio"])
        or len(b.subs_houden) < len(info["subs"])
        or not info["pad"].lower().endswith(".mkv")
    )


if __name__ == "__main__":
    import json
    import sys

    from app.media.probe import probe

    if len(sys.argv) < 2:
        sys.exit("Gebruik: python3 beslissen.py <bestand>")
    info = probe(sys.argv[1])
    if not info:
        sys.exit("Niet te lezen")
    print(json.dumps(beslis(info).model_dump(), indent=2, ensure_ascii=False))
