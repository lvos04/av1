# ─────────────────────────────────────────────────────────────────────
#  GEDEELDE BESTANDSFILTER  —  automatisch gegenereerd, niet bewerken
#
#  Deze blok is identiek in alle drie de plugins. Pas hem aan in
#  _bron/filter.py en draai scripts/installeer.sh; die controleert of de
#  drie kopieen gelijk zijn en weigert te installeren als ze uit elkaar
#  lopen.
#
#  WAAROM DE VORIGE VERSIE GEVAARLIJK WAS
#
#  De oude filter deed dit:
#
#      if stam_laag.endswith(s) or s.strip("-") in stam_laag.split("."):
#
#  Dat tweede deel keek of het woord ergens tussen de punten stond. In
#  scene-naamgeving staat de filmtitel zelf tussen punten, dus:
#
#      Trailer.Park.Boys.S01E01.1080p.mkv  ->  "extra" (trailer)
#      Short.Term.12.2013.1080p.BluRay.mkv ->  "extra" (short)
#      Interview.with.the.Vampire.1994.mkv ->  "extra" (interview)
#      Other.People.2016.1080p.WEBRip.mkv  ->  "extra" (other)
#      Sample.This.2012.1080p.mkv          ->  "extra" (sample)
#
#  Met de opruimplugin op "verwijderen" waren dat vijf verwijderde films.
#
#  Nu geldt:
#    - Jellyfin-achtervoegsels tellen alleen als ACHTERVOEGSEL. Dat is ook
#      precies wat Jellyfin zelf doet: film-trailer.mkv, niet Trailer.Park.
#    - Losse rommelwoorden tellen alleen als LAATSTE woord, of als het hele
#      bestand niets anders heet (RARBG.mp4).
#    - Nooit als eerste woord, want daar staat de titel.
# ─────────────────────────────────────────────────────────────────────

import os
import subprocess
from collections.abc import Sequence

# Jellyfin's vaste achtervoegsels voor extra's.
JELLYFIN_EXTRAS = (
    "-trailer",
    "-behindthescenes",
    "-deleted",
    "-deletedscene",
    "-featurette",
    "-interview",
    "-scene",
    "-short",
    "-clip",
    "-other",
    "-extra",
    "-sample",
)

# Woorden die op zichzelf een niet-hoofdbestand aanwijzen, maar ALLEEN
# achteraan de naam. "sample" in "Sample.This.2012" is een filmtitel.
LOSSE_WOORDEN = ("sample", "trailer", "proof", "screens", "rarbg", "etrg")

EXTRA_MAPPEN = (
    "extras",
    "featurettes",
    "behind the scenes",
    "behind-the-scenes",
    "deleted scenes",
    "deletedscenes",
    "interviews",
    "scenes",
    "shorts",
    "trailers",
    "sample",
    "samples",
    "other",
)
# LET OP: "specials" staat er bewust NIET in. Dat is bij series en anime
# seizoen 0 met echte afleveringen en OVA's - die wil je juist wel verwerken.
# Wil je ze toch overslaan, zet "specials" dan in extra_mappen.

VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv")


def _tokens(stam_laag: str) -> list[str]:
    """Splitst een bestandsnaam in woorden op punt, spatie, underscore en streep."""
    tekst = stam_laag
    for teken in (" ", "_", "-"):
        tekst = tekst.replace(teken, ".")
    return [t for t in tekst.split(".") if t]


def is_hoofdbestand(
    path: str,
    min_duur_sec: float,
    extra_mappen: Sequence[str] | None = None,
    extra_namen: Sequence[str] | None = None,
) -> tuple[bool, str, str]:
    """Geeft (verwerken: bool, reden: str, categorie: str) terug.

    Filtert alles weg wat geen echte film of aflevering is: trailers, samples,
    extra's, en bijbestanden. Die horen niet in de wachtrij.

    De categorie bepaalt of een bestand veilig te VERWIJDEREN is:
        "ok"        hoofdbestand, verwerken
        "extra"     zekere treffer op naam of map - mag weg
        "kort"      alleen op duur afgekeurd - NOOIT verwijderen, want dat
                    kan een korte anime-aflevering of een OVA zijn
        "geen_video" ander bestandstype, met rust laten
    """
    naam = os.path.basename(path)
    stam, ext = os.path.splitext(naam)
    stam_laag = stam.lower()

    # 1. Is het uberhaupt een videobestand?
    if ext.lower() not in VIDEO_EXT:
        return False, "geen videobestand ({})".format(ext or "zonder extensie"), "geen_video"

    # 2. Jellyfin-achtervoegsels. ALLEEN als achtervoegsel, want dat is de
    #    afspraak: "Dune (2024)-trailer.mkv". Een titel die toevallig met
    #    hetzelfde woord begint is geen extra.
    achtervoegsels = list(JELLYFIN_EXTRAS)
    if extra_namen:
        achtervoegsels += [x for x in extra_namen if x]
    for suffix in achtervoegsels:
        s = suffix.strip().lower()
        if not s:
            continue
        if stam_laag.endswith(s):
            return False, f"extra-bestand (achtervoegsel '{s}')", "extra"

    # 3. Losse rommelwoorden, alleen achteraan of als de hele naam.
    woorden = _tokens(stam_laag)
    if woorden:
        for woord in LOSSE_WOORDEN:
            # Hele naam is het woord: "RARBG.mp4", "sample.mkv"
            if len(woorden) == 1 and woorden[0] == woord:
                return False, f"bestandsnaam is '{woord}'", "extra"
            # Laatste woord: "Oppenheimer.2023.1080p.sample.mkv"
            if len(woorden) > 1 and woorden[-1] == woord:
                return False, f"naam eindigt op '{woord}'", "extra"

    # 4. Ligt het in een extra-map?
    mappen = [m.lower() for m in os.path.dirname(path).split(os.sep) if m]
    uit = list(EXTRA_MAPPEN)
    if extra_mappen:
        uit += [x.strip().lower() for x in extra_mappen if x.strip()]
    for m in mappen:
        if m in uit:
            return False, f"staat in map '{m}'", "extra"

    # 5. Duurcheck als vangnet: samples en trailers zijn kort.
    #    Levert bewust categorie "kort" op en niet "extra", zodat de
    #    opruimplugin hier nooit op verwijdert.
    if min_duur_sec > 0:
        try:
            r = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if r.returncode == 0 and r.stdout.strip():
                duur = float(r.stdout.strip())
                if duur < min_duur_sec:
                    return (
                        False,
                        f"te kort ({duur:.0f}s, drempel {min_duur_sec:.0f}s)",
                        "kort",
                    )
        except (subprocess.SubprocessError, ValueError, OSError):
            pass  # geen duur te bepalen; laat de rest van de flow beslissen

    return True, "hoofdbestand", "ok"


def veilig_om_op_te_ruimen(path: str, max_mb: float) -> tuple[bool, str]:
    """Tweede slot op de deur, voor de opruimplugin.

    De naamcontrole hierboven kan er in theorie nog steeds naast zitten bij
    een naamgeving die niemand heeft voorzien. Deze functie kijkt daarom niet
    naar de naam maar naar de omgeving van het bestand, en houdt drie dingen
    tegen die een echte film of aflevering kenmerken:

      1. Het is de enige video in zijn map. Een trailer ligt naast een film,
         nooit alleen.
      2. Het is de grootste video in zijn map. Extra's zijn per definitie het
         kleine broertje.
      3. Het is groter dan max_mb. Een trailer van 30 GB bestaat niet.

    Uitzondering: staat het bestand in een echte extra-map (Extras,
    Featurettes, Trailers), dan is de map zelf al het bewijs en gelden
    regel 1 en 2 niet - daar liggen alleen maar extra's.

    Geeft (mag_weg: bool, reden: str) terug.
    """
    mappen = [m.lower() for m in os.path.dirname(path).split(os.sep) if m]
    in_extra_map = any(m in EXTRA_MAPPEN for m in mappen)

    try:
        eigen_grootte = os.path.getsize(path)
    except OSError as exc:
        return False, f"kan grootte niet lezen ({exc})"

    max_bytes = float(max_mb) * 1024 * 1024
    if max_bytes > 0 and eigen_grootte > max_bytes:
        grootte_mb = eigen_grootte / (1024 * 1024)
        return (
            False,
            f"te groot om een extra te zijn ({grootte_mb:.0f} MB, grens {float(max_mb):.0f} MB)",
        )

    if in_extra_map:
        return True, "in een extra-map"

    map_ = os.path.dirname(path)
    try:
        videos = [
            os.path.join(map_, n)
            for n in os.listdir(map_)
            if os.path.splitext(n)[1].lower() in VIDEO_EXT and os.path.isfile(os.path.join(map_, n))
        ]
    except OSError as exc:
        return False, f"kan map niet lezen ({exc})"

    if len(videos) <= 1:
        return False, "enige video in de map - dit is waarschijnlijk het hoofdbestand"

    groottes = []
    for v in videos:
        try:
            groottes.append(os.path.getsize(v))
        except OSError:
            pass
    if groottes and eigen_grootte >= max(groottes):
        return False, "grootste video in de map - dit is waarschijnlijk het hoofdbestand"

    return True, "kleiner dan het hoofdbestand in dezelfde map"
