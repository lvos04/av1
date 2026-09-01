#!/usr/bin/env python3
"""Begrensde root-helper: mount één gevalideerde SMB-share onder /library/netwerk."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from fcntl import LOCK_EX, flock
from pathlib import Path
from typing import Any

NETWORK_ROOT = Path("/library/netwerk")
CREDENTIAL_ROOT = Path("/etc/av1-pipeline-smb")
UNIT_ROOT = Path("/etc/systemd/system")
LOCK_PATH = Path("/run/lock/av1-pipeline-smb-helper.lock")
SERVER_PATTERN = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


class HelperError(RuntimeError):
    """Invoer of mountactie valt buiten de toegestane helpergrenzen."""


def run(arguments: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def require_program(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise HelperError(f"Vereist programma ontbreekt: {name}")
    return path


def validate_private_server(server: str) -> tuple[str, str]:
    normalized = server.lower().rstrip(".")
    if not SERVER_PATTERN.fullmatch(normalized) or ".." in normalized:
        raise HelperError("Ongeldige SMB-servernaam")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(normalized, 445, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise HelperError(f"SMB-server kan niet worden gevonden: {normalized}") from exc
    if not addresses:
        raise HelperError("SMB-server heeft geen netwerkadres")
    validated: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for value in addresses:
        address = ipaddress.ip_address(value)
        if (
            not (address.is_private or address.is_link_local)
            or address.is_loopback
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise HelperError(
                "SMB-mounts zijn alleen toegestaan naar privé-netwerkadressen"
            )
        validated.append(address)
    # Een vast gevalideerd IP in de mountopties voorkomt dat een DNS-wijziging
    # tussen controle en mount de root-helper naar een ander adres stuurt.
    selected = min(validated, key=lambda address: (address.version, str(address)))
    return normalized, str(selected)


def validate_share(value: str) -> str:
    share = value.strip()
    forbidden = set('/\\[]:|<>+=;,?*"')
    if (
        not share
        or len(share) > 80
        or share in {".", ".."}
        or share != value
        or any(ord(character) < 32 or character in forbidden for character in share)
    ):
        raise HelperError("Ongeldige SMB-sharenaam")
    return share


def unit_value(value: str) -> str:
    """Escapeer data voor één systemd-unitregel, inclusief %-specifiers."""
    return (
        value.replace("%", "%%")
        .replace("\\", "\\x5c")
        .replace(" ", "\\x20")
        .replace("\t", "\\x09")
    )


def read_request() -> dict[str, Any]:
    raw = sys.stdin.read(16_385)
    if len(raw) > 16_384:
        raise HelperError("SMB-verzoek is te groot")
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HelperError("SMB-verzoek bevat geen geldige JSON") from exc
    if not isinstance(value, dict):
        raise HelperError("SMB-verzoek moet een object zijn")
    if set(value) - {"server", "share", "username", "password", "guest"}:
        raise HelperError("SMB-verzoek bevat onbekende velden")
    return value


def atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def exclusive_lock() -> Iterator[None]:
    descriptor = os.open(LOCK_PATH, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        flock(descriptor, LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def mounted_filesystems(mount_path: Path, findmnt: str) -> list[tuple[str, str]]:
    result = run(
        [
            findmnt,
            "-J",
            "-o",
            "SOURCE,FSTYPE,TARGET",
            "--mountpoint",
            str(mount_path),
        ]
    )
    if result.returncode != 0:
        return []
    try:
        payload: Any = json.loads(result.stdout)
        filesystems = payload["filesystems"]
        if not isinstance(filesystems, list):
            raise TypeError
        return [
            (str(filesystem["source"]).rstrip("/"), str(filesystem["fstype"]))
            for filesystem in filesystems
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HelperError("Kon de beheerde mountidentiteit niet lezen") from exc


def exact_cifs_mounted(
    filesystems: list[tuple[str, str]], expected_source: str
) -> bool:
    """Negeer de normale autofs-laag, maar nooit een ander echt filesystem."""
    actual = [(source, kind) for source, kind in filesystems if kind != "autofs"]
    if not actual:
        return False
    if all(
        kind == "cifs" and source.casefold() == expected_source.casefold()
        for source, kind in actual
    ):
        return True
    raise HelperError("Beheerd mountpad bevat al een andere bron")


def unit_name(mount_path: Path, systemd_escape: str) -> str:
    result = run([systemd_escape, "--path", "--suffix=mount", str(mount_path)])
    if result.returncode != 0 or not result.stdout.strip().endswith(".mount"):
        raise HelperError("Kon geen veilige systemd-mountnaam maken")
    return result.stdout.strip()


def main() -> None:
    if os.geteuid() != 0:
        raise HelperError(
            "Deze helper moet door de vaste systemd-socketservice draaien"
        )
    request = read_request()
    server_value = request.get("server")
    share_value = request.get("share")
    username_value = request.get("username", "")
    password_value = request.get("password", "")
    guest_value = request.get("guest", False)
    if not isinstance(server_value, str) or not isinstance(share_value, str):
        raise HelperError("Server en share zijn verplicht")
    if not isinstance(username_value, str) or not isinstance(password_value, str):
        raise HelperError("SMB-accountvelden moeten tekst zijn")
    if not isinstance(guest_value, bool):
        raise HelperError("Ongeldige gasttoegangskeuze")
    if any(
        "\n" in value or "\x00" in value for value in (username_value, password_value)
    ):
        raise HelperError("SMB-accountvelden bevatten een verboden teken")
    if len(username_value) > 256 or len(password_value) > 1024:
        raise HelperError("SMB-accountveld is te lang")

    server, server_ip = validate_private_server(server_value)
    share = validate_share(share_value)
    if guest_value and (username_value or password_value):
        raise HelperError("Kies gasttoegang of een account, niet beide")

    mount_cifs = require_program("mount.cifs")
    systemctl = require_program("systemctl")
    systemd_escape = require_program("systemd-escape")
    findmnt = require_program("findmnt")
    del mount_cifs  # Aanwezigheid is gecontroleerd; systemd voert de mount uit.

    identifier = hashlib.sha256(f"{server}/{share}".encode()).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "-", f"{server}-{share}".lower()).strip("-")[:48]
    mount_path = NETWORK_ROOT / f"{slug}-{identifier}"
    expected_source = f"//{server}/{share}"
    NETWORK_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chmod(NETWORK_ROOT, 0o755)
    mount_unit = unit_name(mount_path, systemd_escape)
    automount_unit = mount_unit.removesuffix(".mount") + ".automount"

    current_filesystems = mounted_filesystems(mount_path, findmnt)
    if exact_cifs_mounted(current_filesystems, expected_source):
        print(json.dumps({"mount_path": str(mount_path), "source": expected_source}))
        return
    if any(kind == "autofs" for _source, kind in current_filesystems):
        stop_result = run([systemctl, "stop", automount_unit], timeout=45)
        if stop_result.returncode != 0:
            raise HelperError("Bestaande SMB-automount kon niet worden herstart")
    mount_path.mkdir(mode=0o755, exist_ok=True)
    os.chmod(mount_path, 0o755)

    service = pwd.getpwnam("av1pipeline")
    credential_path = CREDENTIAL_ROOT / f"{identifier}.credentials"
    auth_option: str
    if username_value:
        if not password_value:
            raise HelperError("Vul voor een nieuwe SMB-share ook het wachtwoord in")
        CREDENTIAL_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(
            credential_path,
            f"username={username_value}\npassword={password_value}\n",
            0o600,
        )
        auth_option = f"credentials={credential_path}"
    elif guest_value:
        auth_option = "guest"
    elif credential_path.is_file():
        auth_option = f"credentials={credential_path}"
    else:
        raise HelperError("Vul een SMB-account in of kies expliciet gasttoegang")

    mount_options = ",".join(
        (
            auth_option,
            "vers=3.1.1",
            "iocharset=utf8",
            f"uid={service.pw_uid}",
            f"gid={service.pw_gid}",
            "forceuid",
            "forcegid",
            "file_mode=0660",
            "dir_mode=0770",
            "noperm",
            "serverino",
            f"ip={server_ip}",
            "nosuid",
            "nodev",
            "noexec",
        )
    )
    mount_content = (
        "[Unit]\nDescription=AV1 Pipeline SMB-bron\n"
        "After=network-online.target\nWants=network-online.target\n\n"
        f"[Mount]\nWhat={unit_value(expected_source)}\nWhere={mount_path}\nType=cifs\n"
        f"Options={unit_value(mount_options)}\nTimeoutSec=30\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )
    automount_content = (
        "[Unit]\nDescription=AV1 Pipeline SMB-automount\n"
        "After=network-online.target\nWants=network-online.target\n\n"
        f"[Automount]\nWhere={mount_path}\nTimeoutIdleSec=0\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )
    atomic_write(UNIT_ROOT / mount_unit, mount_content, 0o644)
    atomic_write(UNIT_ROOT / automount_unit, automount_content, 0o644)
    reload_result = run([systemctl, "daemon-reload"])
    if reload_result.returncode != 0:
        raise HelperError("systemd kon de SMB-units niet herladen")
    run([systemctl, "reset-failed", mount_unit])
    enable_result = run([systemctl, "enable", "--now", automount_unit], timeout=45)
    if enable_result.returncode != 0:
        raise HelperError(
            enable_result.stderr.strip() or "SMB-automount starten mislukt"
        )
    try:
        with os.scandir(mount_path) as entries:
            next(entries, None)
    except OSError as exc:
        raise HelperError(f"SMB-share kon niet worden geopend: {exc}") from exc
    current_filesystems = mounted_filesystems(mount_path, findmnt)
    if not exact_cifs_mounted(current_filesystems, expected_source):
        status_result = run([systemctl, "status", "--no-pager", mount_unit])
        message = status_result.stdout.strip().splitlines()[-1:] or [
            "onbekende mountfout"
        ]
        raise HelperError(f"SMB-share is niet gemount: {message[0]}")
    print(json.dumps({"mount_path": str(mount_path), "source": expected_source}))


if __name__ == "__main__":
    try:
        with exclusive_lock():
            main()
    except (HelperError, OSError, subprocess.SubprocessError) as exc:
        # De socketclient krijgt uitsluitend deze begrensde fouttekst terug;
        # verzoekdata en accountgeheimen worden nooit gelogd of gespiegeld.
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(1) from exc
