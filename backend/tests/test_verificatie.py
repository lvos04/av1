from pathlib import Path

import pytest

from app.media.beslissen import Besluit
from app.verificatie import verify_result


def _source(content_light: str = "0,0") -> dict[str, object]:
    return {
        "is_hdr10": True,
        "hdr10plus": False,
        "mastering_display": "G(0.2650,0.6900)L(1000.0000,0.0001)",
        "content_light": content_light,
        "dv_profile": 8,
        "audio": [{"index": 1}],
        "attachments": 0,
    }


def _result(content_light: str = "") -> dict[str, object]:
    return {
        "is_hdr10": True,
        "mastering_display": "G(0.2650,0.6900)L(1000.0000,0.0001)",
        "content_light": content_light,
        "dv_profile": 10,
        "audio": [{"index": 1}],
        "attachments": 0,
        "size": 50,
    }


def test_nul_content_light_en_ontbrekende_record_zijn_semantisch_gelijk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.verificatie.probe", lambda _path: _result(""))

    verification = verify_result(
        _source("0,0"),
        Path("resultaat.mkv"),
        100,
        Besluit(actie="hercoderen", audio_houden=[1]),
    )

    assert verification.passed is True
    assert verification.mastering_equal is True


@pytest.mark.parametrize("source,result", [("1000,400", ""), ("0,0", "1,0")])
def test_iedere_niet_nul_content_light_afwijking_blijft_geblokkeerd(
    monkeypatch: pytest.MonkeyPatch, source: str, result: str
) -> None:
    monkeypatch.setattr("app.verificatie.probe", lambda _path: _result(result))

    verification = verify_result(
        _source(source),
        Path("resultaat.mkv"),
        100,
        Besluit(actie="hercoderen", audio_houden=[1]),
    )

    assert verification.passed is False
    assert verification.mastering_equal is False
    assert verification.reason == "HDR10 mastering display of content light is gewijzigd"


def test_mastering_display_afwijking_blijft_geblokkeerd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _result("")
    result["mastering_display"] = "andere waarden"
    monkeypatch.setattr("app.verificatie.probe", lambda _path: result)

    verification = verify_result(
        _source("0,0"),
        Path("resultaat.mkv"),
        100,
        Besluit(actie="hercoderen", audio_houden=[1]),
    )

    assert verification.passed is False
    assert verification.mastering_equal is False
