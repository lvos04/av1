from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.bronnen import (
    SourceError,
    ensure_smb_mount,
    parse_smb_location,
    resolve_local_source,
    validate_registered_source,
)
from app.bronnen import test_source as controleer_bron
from app.config import settings


def test_smb_uri_heeft_vaste_share_en_vrije_submap() -> None:
    location = parse_smb_location("smb://TrueNAS.local/jelly/Media/Films met spatie")

    assert location.server == "truenas.local"
    assert location.share == "jelly"
    assert location.subpath.as_posix() == "Media/Films met spatie"
    assert location.uri == "smb://truenas.local/jelly/Media/Films%20met%20spatie"


@pytest.mark.parametrize(
    "value",
    (
        "smb://naam:wachtwoord@server/share/map",
        "smb://server/share/%2e%2e/geheim",
        "smb://server/share/verborgen%2F..%2Fgeheim",
        "https://server/share/map",
        "smb://server",
    ),
)
def test_smb_uri_weigert_geheimen_padtraversal_en_andere_protocollen(value: str) -> None:
    with pytest.raises(SourceError):
        parse_smb_location(value)


def test_lokale_bron_mag_buiten_library_staan_maar_nooit_in_pipeline(
    tmp_path: Path,
) -> None:
    pipeline = tmp_path / "pipeline"
    pipeline.mkdir()
    media = tmp_path / "willekeurige-media"
    media.mkdir()
    configured = replace(
        settings,
        pipeline_root=pipeline,
        cache_root=pipeline / "cache",
        work_root=pipeline / "work",
        config_root=pipeline / "config",
        backup_root=pipeline / "backups",
    )

    assert resolve_local_source(configured, str(media)) == media
    with pytest.raises(SourceError, match="pipelineopslag"):
        resolve_local_source(configured, str(pipeline))


def test_eerste_gebruik_test_laat_geen_tijdelijke_bestanden_achter(tmp_path: Path) -> None:
    report = controleer_bron(tmp_path)

    assert report["geslaagd"] is True
    assert report["atomisch_hernoemen"] is True
    assert report["extended_acl"] is True
    assert report["xattrs"] is True
    assert list(tmp_path.iterdir()) == []


def test_smb_bron_wordt_bij_scan_op_exacte_mountidentiteit_getest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def mounted(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "filesystems": [
                        {
                            "source": "systemd-1",
                            "fstype": "autofs",
                            "target": str(tmp_path),
                        },
                        {
                            "source": "//truenas.local/jelly",
                            "fstype": "cifs",
                            "target": str(tmp_path),
                        },
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("app.bronnen.subprocess.run", mounted)

    assert (
        validate_registered_source(
            tmp_path,
            source_type="smb",
            source_uri="smb://truenas.local/jelly/Media/test",
            expected_device=1,
        )
        == tmp_path.stat().st_dev
    )


def test_smb_bron_stopt_als_onderliggende_mountmap_zichtbaar_wordt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unmounted(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {"filesystems": [{"source": "/dev/sda2", "fstype": "ext4", "target": "/"}]}
            ),
            stderr="",
        )

    monkeypatch.setattr("app.bronnen.subprocess.run", unmounted)

    with pytest.raises(SourceError, match="niet gemount"):
        validate_registered_source(
            tmp_path,
            source_type="smb",
            source_uri="smb://truenas.local/jelly/Media/test",
            expected_device=tmp_path.stat().st_dev,
        )


def test_backend_benadert_alleen_de_vaste_smb_helpersocket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "smb-helper.sock"
    socket_path.touch()
    sent = bytearray()

    class FakeSocket:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.responses = iter((b'{"mount_path":"/library/netwerk/truenas-jelly-test"}', b""))

        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def settimeout(self, _timeout: int) -> None:
            return None

        def connect(self, path: str) -> None:
            assert path == str(socket_path)

        def sendall(self, value: bytes) -> None:
            sent.extend(value)

        def shutdown(self, _direction: int) -> None:
            return None

        def recv(self, _size: int) -> bytes:
            return next(self.responses)

    monkeypatch.setattr("app.bronnen.SMB_HELPER_SOCKET", socket_path)
    monkeypatch.setattr("app.bronnen.socket.socket", FakeSocket)

    result = ensure_smb_mount(
        parse_smb_location("smb://truenas.local/jelly/Media/test"),
        username="media",
        password="niet-loggen",
        guest=False,
    )

    assert result == Path("/library/netwerk/truenas-jelly-test/Media/test")
    assert json.loads(sent)["password"] == "niet-loggen"
