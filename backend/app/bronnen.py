"""Veilige eerste-gebruikcontrole voor lokale mappen en blijvende SMB-mounts."""

from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import stat
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from app.bestandsmetadata import MetadataError, apply_metadata, capture_metadata
from app.config import Settings

SMB_HELPER_SOCKET = Path("/run/av1-pipeline/smb-helper.sock")
PROTECTED_ROOTS = tuple(
    Path(value)
    for value in (
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/sbin",
        "/sys",
        "/tmp",
        "/usr",
        "/var/cache",
        "/var/lib",
        "/var/log",
        "/var/spool",
    )
)


class SourceError(RuntimeError):
    """Een bron is onveilig, onbereikbaar of niet compatibel."""


@dataclass(frozen=True, slots=True)
class SmbLocation:
    uri: str
    server: str
    share: str
    subpath: PurePosixPath


def parse_smb_location(raw: str) -> SmbLocation:
    """Ontleed alleen smb://server/share/submap zonder geheimen in de URL."""
    parsed = urlsplit(raw.strip())
    if parsed.scheme.lower() != "smb" or not parsed.hostname:
        raise SourceError("Gebruik een SMB-adres zoals smb://server/share/map")
    if parsed.username is not None or parsed.password is not None:
        raise SourceError("Zet gebruikersnaam of wachtwoord nooit in het SMB-adres")
    if parsed.query or parsed.fragment:
        raise SourceError("Een SMB-adres mag geen query of fragment bevatten")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if not parts:
        raise SourceError("Het SMB-adres mist een sharenaam")
    if any(
        part in {".", ".."}
        or any(ord(character) < 32 for character in part)
        or "/" in part
        or "\\" in part
        for part in parts
    ):
        raise SourceError("Het SMB-adres bevat een onveilig padsegment")
    server = parsed.hostname.lower().rstrip(".")
    share = parts[0]
    subpath = PurePosixPath(*parts[1:]) if len(parts) > 1 else PurePosixPath(".")
    normalized_path = "/".join(quote(part, safe="!$&'()*+,-.:;=@_~") for part in parts)
    return SmbLocation(
        uri=f"smb://{server}/{normalized_path}",
        server=server,
        share=share,
        subpath=subpath,
    )


def resolve_local_source(settings: Settings, raw: str) -> Path:
    """Sta iedere echte mediamap toe, behalve systeem- en pipelinewortels."""
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        raise SourceError("Een lokale map moet een absoluut pad hebben")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SourceError(f"De lokale map is niet bereikbaar: {exc}") from exc
    if not resolved.is_dir():
        raise SourceError("De gekozen locatie is geen map")
    if resolved == Path("/") or any(
        resolved == root or root.is_relative_to(resolved) for root in PROTECTED_ROOTS
    ):
        raise SourceError("Kies een mediamap, niet een beschermde systeemmap")
    managed = (
        settings.pipeline_root.resolve(),
        settings.cache_root.resolve(),
        settings.work_root.resolve(),
        settings.config_root.resolve(),
        settings.backup_root.resolve(),
    )
    if any(
        resolved == path or resolved.is_relative_to(path) or path.is_relative_to(resolved)
        for path in managed
    ):
        raise SourceError("Een bibliotheek mag de pipelineopslag niet bevatten of overlappen")
    if not os.access(resolved, os.R_OK | os.W_OK | os.X_OK):
        raise SourceError(
            "De backendservice moet de map kunnen lezen, schrijven en openen; "
            "pas eerst de maprechten aan"
        )
    return resolved


def ensure_smb_mount(
    location: SmbLocation,
    *,
    username: str | None,
    password: str | None,
    guest: bool,
) -> Path:
    """Vraag de begrensde rootservice via zijn lokale Unix-socket om een mount."""
    if not SMB_HELPER_SOCKET.exists():
        raise SourceError("De SMB-helperservice is nog niet op deze host geïnstalleerd")
    payload = {
        "server": location.server,
        "share": location.share,
        "username": username or "",
        "password": password or "",
        "guest": guest,
    }
    try:
        encoded = json.dumps(payload).encode("utf-8")
        response_bytes = bytearray()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(60)
            connection.connect(str(SMB_HELPER_SOCKET))
            connection.sendall(encoded)
            connection.shutdown(socket.SHUT_WR)
            while block := connection.recv(4096):
                response_bytes.extend(block)
                if len(response_bytes) > 16_384:
                    raise SourceError("SMB-helper gaf een te groot antwoord terug")
    except (OSError, TimeoutError) as exc:
        raise SourceError(f"SMB-mounthelper kon niet worden bereikt: {exc}") from exc
    try:
        response: Any = json.loads(response_bytes.decode("utf-8"))
        if isinstance(response, dict) and response.get("error"):
            raise SourceError(str(response["error"])[-1000:])
        mount_path = Path(str(response["mount_path"]))
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SourceError("SMB-helper gaf geen geldig resultaat terug") from exc
    expected_root = Path("/library/netwerk")
    if not mount_path.is_absolute() or not mount_path.is_relative_to(expected_root):
        raise SourceError("SMB-helper gaf een pad buiten de beheerde netwerkroot terug")
    target = mount_path.joinpath(*location.subpath.parts).resolve(strict=False)
    if not target.is_relative_to(mount_path):
        raise SourceError("SMB-submap valt buiten de gemounte share")
    return target


def _supports_test_xattr(path: Path) -> bool:
    try:
        os.setxattr(path, "user.av1_pipeline_controle", b"behouden", follow_symlinks=False)
        return True
    except OSError as exc:
        if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM}:
            return False
        raise


def _supports_extended_acl(path: Path) -> bool:
    result = subprocess.run(
        ["setfacl", "-m", "u:1:r--", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def test_source(path: Path) -> dict[str, Any]:
    """Test dezelfde kritieke bewerkingen als een latere veilige vervanging."""
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceError(f"De gekozen map is niet bereikbaar: {exc}") from exc
    if not resolved.is_dir() or not os.access(resolved, os.R_OK | os.W_OK | os.X_OK):
        raise SourceError("De backendservice kan de gekozen map niet lezen en schrijven")
    token = uuid.uuid4().hex
    source = resolved / f".av1-broncontrole-{token}.bron"
    candidate = resolved / f".av1-broncontrole-{token}.nieuw"
    xattrs = False
    extended_acl = False
    try:
        descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(b"AV1 Pipeline broncontrole\n")
            handle.flush()
            os.fsync(handle.fileno())
        xattrs = _supports_test_xattr(source)
        extended_acl = _supports_extended_acl(source)
        original_metadata = capture_metadata(source)
        shutil.copyfile(source, candidate)
        with candidate.open("ab") as handle:
            handle.write(b"Vervangresultaat\n")
        apply_metadata(candidate, original_metadata)
        if candidate.stat().st_dev != source.stat().st_dev:
            raise SourceError("Tijdelijk resultaat en bron staan niet op hetzelfde filesystem")
        with candidate.open("rb") as handle:
            os.fsync(handle.fileno())
        _fsync_directory(resolved)
        os.replace(candidate, source)
        _fsync_directory(resolved)
        if source.read_bytes() != (b"AV1 Pipeline broncontrole\nVervangresultaat\n"):
            raise SourceError("Inhoud wijkt af na atomisch hernoemen")
        if capture_metadata(source) != original_metadata:
            raise SourceError("Bestandsrechten of metadata wijken af na de vervangtest")
    except (OSError, MetadataError, subprocess.SubprocessError) as exc:
        raise SourceError(f"Eerste-gebruikcontrole mislukt: {exc}") from exc
    finally:
        cleanup_error: OSError | None = None
        for temporary in (source, candidate):
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise SourceError(
                f"Tijdelijke broncontrole kon niet worden opgeruimd: {cleanup_error}"
            ) from cleanup_error
    file_stat = resolved.stat()
    return {
        "geslaagd": True,
        "pad": str(resolved),
        "device": file_stat.st_dev,
        "atomisch_hernoemen": True,
        "basisrechten": stat.S_IMODE(file_stat.st_mode),
        "extended_acl": extended_acl,
        "xattrs": xattrs,
        "waarschuwing": (
            None
            if extended_acl and xattrs
            else (
                "Deze bron toont vaste of beperkte netwerkmetadata; iedere zichtbare "
                "metadata-afwijking blijft per job hard falen."
            )
        ),
    }


def validate_registered_source(
    path: Path,
    *,
    source_type: str,
    source_uri: str | None,
    expected_device: int | None,
) -> int:
    """Voorkom dat een verdwenen mount stil als de onderliggende rootmap wordt gelezen."""
    try:
        resolved = path.resolve(strict=True)
        current_device = resolved.stat().st_dev
    except OSError as exc:
        raise SourceError(f"Bibliotheekbron is niet bereikbaar: {exc}") from exc
    if source_type == "smb":
        if source_uri is None:
            raise SourceError("SMB-bibliotheek mist de opgeslagen bron-URI")
        location = parse_smb_location(source_uri)
        try:
            result = subprocess.run(
                ["findmnt", "-J", "-o", "SOURCE,FSTYPE,TARGET", "-T", str(resolved)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SourceError(f"SMB-mountidentiteit kon niet worden gelezen: {exc}") from exc
        if result.returncode != 0:
            raise SourceError("SMB-mountidentiteit kan niet worden vastgesteld")
        try:
            payload: Any = json.loads(result.stdout)
            filesystems = payload["filesystems"]
            if not isinstance(filesystems, list):
                raise TypeError
            identities = [
                (str(filesystem["source"]).rstrip("/"), str(filesystem["fstype"]))
                for filesystem in filesystems
            ]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourceError("SMB-mountidentiteit kan niet worden vastgesteld") from exc
        expected_source = f"//{location.server}/{location.share}"
        if not any(
            filesystem_type == "cifs" and actual_source.casefold() == expected_source.casefold()
            for actual_source, filesystem_type in identities
        ):
            raise SourceError(
                "SMB-share is niet gemount; scan en uitvoering blijven veilig gestopt"
            )
        return current_device
    if source_type != "lokaal":
        raise SourceError("Onbekend bibliotheekbrontype")
    if expected_device is not None and current_device != expected_device:
        raise SourceError("Lokale bibliotheek staat niet meer op het geteste filesystem")
    return current_device


def _fsync_directory(path: Path) -> None:
    """Gebruik exact dezelfde filesystembarrière als de echte vervanging."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
