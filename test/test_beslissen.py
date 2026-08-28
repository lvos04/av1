"""Tests voor de beslisboom.

Elke test dekt een valkuil uit spec/02-domeinkennis.md af. Gaat er hier een
rood, dan gaat er straks HDR of Dolby Vision verloren op je echte bestanden.
"""
import pytest

from beslissen import beslis
from probe import probe

from conftest import pad

LAAG = {"drempel_2160": 500, "drempel_1080": 500, "drempel_720": 300}


# ── HDR10 ─────────────────────────────────────────────────────────────

def test_hdr10_wordt_herkend():
    """Mastering display zit in FRAME-side-data. Wie alleen streams leest
    ziet dit niet en encodeert de HDR er stilzwijgend uit."""
    i = probe(pad("hdr10.mkv"))
    assert i["is_hdr10"] is True
    assert i["mastering_display"].startswith("G(")
    assert i["content_light"] == "1000,400"
    assert len(i["md_ruw"]) == 10


def test_hdr10_gaat_standaard_naar_de_cpu():
    """av1_qsv kan mastering display niet meedragen. HDR hoort dus naar
    SVT-AV1 op de CPU, tenzij je expliciet anders instelt."""
    b = beslis(probe(pad("hdr10.mkv")), LAAG)
    assert b.actie == "hercoderen"
    assert b.baan == "cpu"
    assert b.encoder == "libsvtav1"
    assert b.behoud_hdr is True


def test_hdr10_via_gpu_vraagt_om_herstel():
    """Zet je HDR toch op de GPU, dan MOET de metadata achteraf terug."""
    b = beslis(probe(pad("hdr10.mkv")), {**LAAG, "gpu_voor_hdr10": True})
    assert b.baan == "gpu"
    assert b.herstel_hdr_na is True
    assert b.waarschuwingen


# ── Overslaan ─────────────────────────────────────────────────────────

def test_lage_bitrate_blijft_ongemoeid():
    """Het bestand dat iedereen vergeet. Hercoderen maakt dit alleen maar
    slechter en kost uren."""
    b = beslis(probe(pad("lage-bitrate.mkv")))
    assert b.actie != "hercoderen"
    assert "drempel" in b.reden


def test_al_av1_wordt_niet_opnieuw_gedaan():
    b = beslis(probe(pad("al-av1.mkv")))
    assert b.actie != "hercoderen"
    assert "av1" in b.reden.lower()


# ── Sporen ────────────────────────────────────────────────────────────

def test_commentaarspoor_gaat_eruit():
    i = probe(pad("sdr-meertalig.mkv"))
    b = beslis(i, LAAG)
    assert len(b.audio_houden) < len(i["audio"])


def test_er_blijft_altijd_audio_over():
    """Veiligheidsnet: een film in een taal die niet in je lijst staat mag
    er niet zonder audio uitkomen."""
    i = probe(pad("sdr-meertalig.mkv"))
    b = beslis(i, {**LAAG, "audio_talen": ["zzz"]})
    assert len(b.audio_houden) >= 1
    assert any("audiospoor" in w for w in b.waarschuwingen)


def test_ass_lettertypen_blijven_behouden():
    """Anime-releases hangen hun typografie als attachment in het bestand.
    Gooi je die weg, dan valt de speler terug op een standaardlettertype."""
    i = probe(pad("anime-ass.mkv"))
    if i["attachments"] == 0:
        pytest.skip("testbestand heeft geen attachment (geen .ttf gevonden)")
    b = beslis(i, LAAG)
    assert b.attachments_houden is True


# ── Containers ────────────────────────────────────────────────────────

def test_mp4_wordt_opgepakt():
    """Verkeerde container is op zichzelf al reden om te remuxen."""
    b = beslis(probe(pad("mp4-movtext.mp4")), {"drempel_720": 100000})
    assert b.actie == "opschonen"
