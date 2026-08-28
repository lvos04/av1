import os
import subprocess
import sys

import pytest

WORTEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORTEL, "startcode"))

TESTDATA = os.environ.get("TESTDATA", os.path.join(WORTEL, "testdata"))


@pytest.fixture(scope="session", autouse=True)
def testbestanden():
    """Maakt de testbestanden als ze er nog niet zijn."""
    if not os.path.isdir(TESTDATA) or not os.listdir(TESTDATA):
        script = os.path.join(WORTEL, "test", "maak-testbestanden.sh")
        subprocess.run(["bash", script, TESTDATA], check=True)
    return TESTDATA


def pad(naam):
    return os.path.join(TESTDATA, naam)
