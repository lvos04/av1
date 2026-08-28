from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

WORTEL = Path(__file__).resolve().parents[2]
TESTDATA = Path(os.environ.get("TESTDATA", str(WORTEL / "testdata")))


@pytest.fixture(scope="session", autouse=True)
def testbestanden() -> str:
    """Genereer uitsluitend de korte representatieve testbestanden."""

    if not TESTDATA.is_dir() or not any(TESTDATA.iterdir()):
        subprocess.run(
            ["bash", str(WORTEL / "test" / "maak-testbestanden.sh"), str(TESTDATA)],
            check=True,
        )
    return str(TESTDATA)


def pad(naam: str) -> str:
    return str(TESTDATA / naam)
