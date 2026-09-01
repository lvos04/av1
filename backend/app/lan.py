"""Wachtwoordcontrole voor de afgeschermde webinterface op het thuisnetwerk."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import socket
from typing import Any

import psutil

LAN_CONFIRMATION = "LAN-TOEGANG INSCHAKELEN"
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_HASH_BYTES = 32


class LanConfigError(ValueError):
    """Een LAN-account voldoet niet aan de vaste veiligheidsgrenzen."""


def validate_username(value: str) -> str:
    """Houd de Basic-Auth-gebruikersnaam voorspelbaar en header-veilig."""
    username = value.strip()
    if not _USERNAME_PATTERN.fullmatch(username):
        raise LanConfigError(
            "Gebruikersnaam moet 3-64 tekens bevatten: letters, cijfers, punt, _ of -"
        )
    return username


def hash_password(password: str) -> str:
    """Sla alleen een geheugenhard afgeleid wachtwoord op, nooit het wachtwoord zelf."""
    if len(password) < 12 or len(password) > 128:
        raise LanConfigError("Wachtwoord moet 12-128 tekens lang zijn")
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_HASH_BYTES,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Controleer een wachtwoord zonder verschillen in de hash te lekken."""
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$")
        if algorithm != "scrypt":
            return False
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        if (n, r, p) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P):
            return False
        salt = base64.b64decode(raw_salt, validate=True)
        expected = base64.b64decode(raw_digest, validate=True)
        if len(salt) != _SALT_BYTES or len(expected) != _HASH_BYTES:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def parse_basic_authorization(header: str | None) -> tuple[str, str] | None:
    """Lees een Basic-header streng zonder de inhoud ergens te loggen."""
    if header is None or not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator or not username or not password:
        return None
    return username, password


def authorization_matches(configuration: dict[str, Any], header: str | None) -> bool:
    """Vergelijk account en wachtwoord uit een verzoek met de opgeslagen hash."""
    credentials = parse_basic_authorization(header)
    username = configuration.get("gebruikersnaam")
    encoded = configuration.get("wachtwoord_hash")
    if credentials is None or not isinstance(username, str) or not isinstance(encoded, str):
        return False
    supplied_username, password = credentials
    return hmac.compare_digest(supplied_username, username) and verify_password(password, encoded)


def lan_urls(port: int = 3000) -> list[str]:
    """Toon bruikbare IPv4-adressen zonder netwerkcommando's of externe diensten."""
    addresses: set[str] = set()
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family != socket.AF_INET or entry.address.startswith("127."):
                continue
            addresses.add(entry.address)
    return [f"http://{address}:{port}" for address in sorted(addresses)]
