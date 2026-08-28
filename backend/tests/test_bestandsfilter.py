"""Tests voor de naamfilter.

Deze filter bepaalt welke bestanden als 'extra' gelden en dus opgeruimd
mogen worden. Een fout hier kost je films. In een eerdere versie van dit
project gold een woord ook als het midden in de titel stond, en omdat
scene-releases hun titel tussen punten zetten werden 'Trailer Park Boys' en
'Short Term 12' als extra aangemerkt.
"""

import pytest

from app.media.bestandsfilter import is_hoofdbestand, veilig_om_op_te_ruimen

ECHTE_FILMS = [
    "Short.Term.12.2013.1080p.BluRay.x264.mkv",
    "Trailer.Park.Boys.S01E01.1080p.mkv",
    "Sample.This.2012.1080p.mkv",
    "Interview.with.the.Vampire.1994.mkv",
    "Other.People.2016.1080p.WEBRip.mkv",
    "Scenes.from.a.Marriage.1973.1080p.mkv",
    "Clip.2012.1080p.mkv",
    "Extraction.2020.2160p.mkv",
    "Deleted.2024.1080p.mkv",
    "The Short Game (2013) Bluray-1080p.mkv",
    "Dune.Part.Two.2024.2160p.UHD.BluRay.mkv",
    "Frieren - S01E01.mkv",
]

ECHTE_EXTRAS = [
    "Dune Part Two (2024)-trailer.mkv",
    "Dune Part Two (2024)-behindthescenes.mkv",
    "Film (2024)-featurette.mkv",
    "Oppenheimer.2023.1080p.sample.mkv",
    "RARBG.mp4",
    "sample.mkv",
]


@pytest.mark.parametrize("naam", ECHTE_FILMS)
def test_echte_films_worden_niet_als_extra_gezien(naam):
    _ok, reden, cat = is_hoofdbestand("/media/films/Map/" + naam, 0)
    assert cat != "extra", f"{naam} werd als extra gezien: {reden}"


@pytest.mark.parametrize("naam", ECHTE_EXTRAS)
def test_echte_extras_worden_herkend(naam):
    _ok, _reden, cat = is_hoofdbestand("/media/films/Map/" + naam, 0)
    assert cat == "extra", f"{naam} werd NIET als extra gezien"


def test_extra_map_telt():
    _ok, _reden, cat = is_hoofdbestand("/media/films/Film (2021)/Extras/Making Of.mkv", 0)
    assert cat == "extra"


def test_specials_wordt_wel_verwerkt():
    """Seizoen 0 bij series en anime bevat echte afleveringen en OVA's."""
    ok, _reden, cat = is_hoofdbestand("/media/series/Show/Specials/S00E01.mkv", 0)
    assert cat == "ok" and ok


def test_bijbestanden_worden_genegeerd():
    for naam in ("poster.jpg", "film.nl.srt", "film.nfo"):
        _ok, _reden, cat = is_hoofdbestand("/media/films/Map/" + naam, 0)
        assert cat == "geen_video"


# ── Tweede slot: kijkt naar de omgeving, niet naar de naam ────────────


def test_enige_video_in_map_blijft_staan(testbestanden):
    """Een trailer ligt naast een film, nooit alleen."""
    import os

    p = os.path.join(
        testbestanden, "mappen", "Short Term 12 (2013)", "Short.Term.12.2013.1080p.BluRay.x264.mkv"
    )
    mag, reden = veilig_om_op_te_ruimen(p, 500)
    assert mag is False
    assert "enige video" in reden


def test_kleinere_video_naast_hoofdfilm_mag_weg(testbestanden):
    import os

    p = os.path.join(
        testbestanden, "mappen", "Dune Part Two (2024)", "Dune Part Two (2024)-trailer.mkv"
    )
    mag, _reden = veilig_om_op_te_ruimen(p, 500)
    assert mag is True


def test_te_groot_blijft_staan(testbestanden):
    """Een trailer van 30 GB bestaat niet."""
    import os

    p = os.path.join(
        testbestanden, "mappen", "Dune Part Two (2024)", "Dune Part Two (2024)-trailer.mkv"
    )
    mag, reden = veilig_om_op_te_ruimen(p, 0.05)  # grens van 50 KB
    assert mag is False
    assert "te groot" in reden
