# ─────────────────────────────────────────────────────────────────────
#  VOORTGANGSMETING  —  automatisch gegenereerd, niet bewerken
#
#  Zonder dit blok blijft de voortgangsbalk in de Unmanic-interface op 0%
#  staan, van begin tot eind. De standaardparser van Unmanic accepteert
#  alleen een kaal getal per regel; de uitvoer van ffmpeg ("frame=  123
#  fps=24 time=00:01:02...") herkent hij niet. Bij een 4K-encode van vier
#  uur zie je dus vier uur lang niets gebeuren.
#
#  ffmpeg krijgt hieronder "-progress pipe:1" mee. Dan schrijft hij regels
#  in de vorm "out_time_ms=62000000" naar stdout, en die zijn wel te lezen.
# ─────────────────────────────────────────────────────────────────────

import re
from collections.abc import Callable

_HANDBRAKE_PCT = re.compile(r"(\d+\.\d+)\s*%")


def ffmpeg_voortgang(duur_seconden: float) -> Callable[[object], dict[str, int]]:
    """Bouwt een parser voor Unmanic's command_progress_parser.

    Unmanic voert elke uitvoerregel aan deze functie en verwacht een dict
    met een 'percent'. Vereist "-progress pipe:1" in het ffmpeg-commando.
    """
    totaal = float(duur_seconden or 0)
    stand = {"percent": 0}

    def parser(line_text: object) -> dict[str, int]:
        tekst = str(line_text).strip()
        if totaal > 0 and tekst.startswith("out_time_ms="):
            try:
                microseconden = float(tekst.split("=", 1)[1])
                pct = int(min(100, max(0, (microseconden / 1000000.0) / totaal * 100)))
                stand["percent"] = pct
            except (ValueError, ZeroDivisionError):
                pass
        elif tekst == "progress=end":
            stand["percent"] = 100
        return {"percent": stand["percent"]}

    return parser


def percentage_voortgang() -> Callable[[object], dict[str, int]]:
    """Voor gereedschap dat zelf een percentage meldt, bv 'task 1 of 1, 12.34 %'.

    Niet nodig voor ffmpeg - die gebruikt ffmpeg_voortgang() hierboven. Blijft
    beschikbaar mocht je ooit een encoder toevoegen die geen -progress kent.
    """
    stand = {"percent": 0}

    def parser(line_text: object) -> dict[str, int]:
        treffer = _HANDBRAKE_PCT.search(str(line_text))
        if treffer:
            try:
                stand["percent"] = int(min(100, max(0, float(treffer.group(1)))))
            except ValueError:
                pass
        return {"percent": stand["percent"]}

    return parser
