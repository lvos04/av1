from __future__ import annotations

import importlib.util
import socket
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def helper() -> ModuleType:
    path = Path(__file__).parents[2] / "install" / "av1-pipeline-smb-helper.py"
    spec = importlib.util.spec_from_file_location("av1_pipeline_smb_helper", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_helper_accepteert_sharenaam_met_spatie(helper: ModuleType) -> None:
    assert helper.validate_share("Media Films$") == "Media Films$"  # type: ignore[attr-defined]
    assert helper.unit_value("//server/Media Films%") == (  # type: ignore[attr-defined]
        "//server/Media\\x20Films%%"
    )


def test_helper_herkent_cifs_onder_de_normale_autofs_laag(helper: ModuleType) -> None:
    assert helper.exact_cifs_mounted(  # type: ignore[attr-defined]
        [
            ("systemd-1", "autofs"),
            ("//truenas.local/jelly", "cifs"),
        ],
        "//truenas.local/jelly",
    )
    assert not helper.exact_cifs_mounted(  # type: ignore[attr-defined]
        [("systemd-1", "autofs")],
        "//truenas.local/jelly",
    )


def test_helper_weigert_een_echt_ander_filesystem_onder_mountpad(
    helper: ModuleType,
) -> None:
    with pytest.raises(RuntimeError, match="andere bron"):
        helper.exact_cifs_mounted(  # type: ignore[attr-defined]
            [("/dev/sda2", "ext4")],
            "//truenas.local/jelly",
        )


@pytest.mark.parametrize("value", ("../media", " Media", "Media/Films", "Media\nFilms"))
def test_helper_weigert_onveilige_sharenamen(helper: ModuleType, value: str) -> None:
    with pytest.raises(RuntimeError):
        helper.validate_share(value)  # type: ignore[attr-defined]


def test_helper_zet_server_vast_op_gevalideerd_prive_ip(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        helper.socket,  # type: ignore[attr-defined]
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.244", 445))
        ],
    )

    assert helper.validate_private_server("TrueNAS.local") == (  # type: ignore[attr-defined]
        "truenas.local",
        "192.168.0.244",
    )


def test_helper_weigert_publiek_serveradres(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        helper.socket,  # type: ignore[attr-defined]
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 445))],
    )

    with pytest.raises(RuntimeError, match="privé-netwerkadressen"):
        helper.validate_private_server("voorbeeld.local")  # type: ignore[attr-defined]
