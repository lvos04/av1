"""Tests die de commando's ECHT uitvoeren en het resultaat controleren.

Dit is het verschil tussen "het commando ziet er goed uit" en "de HDR zit er
nog in". Alleen dat tweede telt.
"""
import os
import subprocess

import pytest

import commandos
from beslissen import STANDAARD, beslis
from probe import probe

from conftest import pad

LAAG = {"drempel_2160": 500, "drempel_1080": 500, "drempel_720": 300}


def draai(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, "commando faalde:\n{}".format(r.stderr[-800:])
    return r


def test_cpu_encode_behoudt_hdr10(tmp_path):
    """De kerntest van het hele project. Gaat deze rood, dan verlies je HDR
    op elk 4K-bestand in je bibliotheek en merk je het pas op je TV."""
    bron = pad("hdr10.mkv")
    uit = str(tmp_path / "uit.mkv")
    i = probe(bron)
    b = beslis(i, LAAG)
    draai(commandos.encode_cpu(i, b, {**STANDAARD, "preset_1080": 12}, bron, uit))

    n = probe(uit)
    assert n["codec"] == "av1"
    assert n["is_hdr10"] is True, "HDR10-metadata is verdwenen"
    assert n["mastering_display"] == i["mastering_display"]
    assert n["content_light"] == i["content_light"]


def test_mp4_met_movtext_faalt_niet(tmp_path):
    """mov_text past niet in Matroska. Zonder omzetting faalt de mux met
    'Subtitle codec 94213 is not supported'."""
    bron = pad("mp4-movtext.mp4")
    uit = str(tmp_path / "uit.mkv")
    i = probe(bron)
    b = beslis(i, {"drempel_720": 100000})
    draai(commandos.opschonen(i, b, bron, uit))

    n = probe(uit)
    assert n is not None
    assert any(s["codec"] in ("subrip", "srt") for s in n["subs"])


def test_opschonen_behoudt_lettertypen(tmp_path):
    bron = pad("anime-ass.mkv")
    i = probe(bron)
    if i["attachments"] == 0:
        pytest.skip("testbestand heeft geen attachment")
    uit = str(tmp_path / "uit.mkv")
    b = beslis(i, LAAG)
    draai(commandos.opschonen(i, b, bron, uit))

    assert probe(uit)["attachments"] == i["attachments"], "lettertypen weg"


def test_hdr_herstel_met_mkvmerge(tmp_path):
    """Zet HDR10-metadata terug op een bestand dat hem niet heeft.

    Dit is het pad dat nodig is na een av1_qsv-encode: die encoder kan
    mastering display niet meedragen. We testen hier de mkvmerge-aanroep
    zelf - in het bijzonder de kleurvolgorde, want mkvmerge wil ROOD-GROEN-
    BLAUW terwijl SVT-AV1 GROEN-BLAUW-ROOD gebruikt. Verwissel je die, dan
    komen er andere getallen terug dan je erin stopte.

    Bewust NIET afhankelijk van hoe een encoder zich gedraagt: nieuwere
    ffmpeg-versies dragen de mastering display vanzelf door, oudere niet.
    """
    if not _heeft("mkvmerge"):
        pytest.skip("mkvmerge niet aanwezig")

    # Een bestand zonder enige HDR-metadata.
    kaal = pad("lage-bitrate.mkv")
    assert probe(kaal)["is_hdr10"] is False, "testbestand had al HDR-metadata"

    # De waarden van het echte HDR10-bestand.
    hdr = probe(pad("hdr10.mkv"))
    assert hdr["is_hdr10"] is True

    uit = str(tmp_path / "hersteld.mkv")
    draai(commandos.herstel_hdr(hdr["md_ruw"], hdr["content_light"], kaal, uit))

    n = probe(uit)
    assert n["is_hdr10"] is True, "mkvmerge schreef geen mastering display"
    assert n["mastering_display"] == hdr["mastering_display"], (
        "de waarden komen anders terug - controleer de kleurvolgorde "
        "(mkvmerge wil R,G,B; SVT-AV1 gebruikt G,B,R)")
    assert n["content_light"] == hdr["content_light"]


def _heeft(tool):
    import shutil
    return shutil.which(tool) is not None


# ── Dolby Vision ──────────────────────────────────────────────────────

def _vereis_dv_testbestand():
    if not os.path.exists(pad("dv81.mkv")):
        pytest.skip("dv81.mkv ontbreekt (dovi_tool niet aanwezig bij het maken)")


def test_dolby_vision_overleeft_de_encode(tmp_path):
    """DV profiel 8.1 in HEVC moet er als AV1 profiel 10 uitkomen.

    Sinds ffmpeg 7.1 doet libsvtav1 dit zelf; HandBrake is niet meer nodig.
    Vereist wel ffmpeg 7.1 of nieuwer.
    """
    _vereis_dv_testbestand()
    bron = pad("dv81.mkv")
    i = probe(bron)
    assert i["dv_profile"] == 8, "testbestand is geen DV profiel 8"

    b = beslis(i, {**LAAG, "dv_modus": "av1"})
    assert b.dolby_vision is True
    assert b.encoder == "libsvtav1"

    uit = str(tmp_path / "uit.mkv")
    draai(commandos.encode_cpu(i, b, {**STANDAARD, "preset_1080": 12,
                                      "crf_1080": 45}, bron, uit))

    n = probe(uit)
    assert n["codec"] == "av1"
    assert n["dv_profile"] == 10, "Dolby Vision is verdwenen"
    assert n["is_hdr10"] is True, "de HDR10-basislaag is verdwenen"
    assert len(n["audio"]) == len(i["audio"]), "audio kwijt"


def test_zonder_expliciete_vlag_gaat_dv_verloren(tmp_path):
    """Vastleggen waarom '-dolbyvision 1' expliciet in het commando staat.

    De standaardstand van libsvtav1 is 'auto', en die schrijft de RPU NIET
    weg. Geen waarschuwing, geen foutmelding. Deze test bewaakt dat we die
    vlag niet 'opruimen' omdat hij overbodig lijkt.
    """
    _vereis_dv_testbestand()
    bron = pad("dv81.mkv")
    uit = str(tmp_path / "auto.mkv")
    draai(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", bron,
           "-map", "0:v:0", "-c:v", "libsvtav1", "-crf", "45", "-preset", "12",
           "-pix_fmt", "yuv420p10le", uit])
    assert probe(uit)["dv_profile"] == 0, (
        "Onverwacht: 'auto' bewaarde de RPU wel. Controleer of de expliciete "
        "vlag nog nodig is in deze ffmpeg-versie.")
