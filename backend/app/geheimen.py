"""Versleutelde opslag van integratiesleutels; ontsleutelde waarden verlaten de backend niet."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.db import Database


class SecretError(RuntimeError):
    """De sleutelbron of opgeslagen ciphertext is ongeldig."""


class SecretStore:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def _fernet(self) -> Fernet:
        try:
            material = self.settings.secret_key_file.read_bytes().strip()
        except OSError as exc:
            raise SecretError("Het geheime sleutelbestand is niet leesbaar") from exc
        if len(material) < 32:
            raise SecretError("Het geheime sleutelbestand is te kort")
        derived = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
        return Fernet(derived)

    def set(self, key: str, value: str) -> None:
        ciphertext = self._fernet().encrypt(value.encode("utf-8")).decode("ascii")
        self.database.execute(
            """
            INSERT INTO geheim(sleutel,ciphertext,gewijzigd_op) VALUES (?,?,?)
            ON CONFLICT(sleutel) DO UPDATE SET ciphertext=excluded.ciphertext,
              gewijzigd_op=excluded.gewijzigd_op
            """,
            (key, ciphertext, datetime.now(UTC).isoformat()),
        )

    def get(self, key: str) -> str | None:
        row = self.database.fetch_one("SELECT ciphertext FROM geheim WHERE sleutel=?", (key,))
        if row is None:
            return None
        try:
            return self._fernet().decrypt(str(row["ciphertext"]).encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise SecretError(f"Opgeslagen geheim '{key}' kan niet worden ontsleuteld") from exc

    def configured(self, key: str) -> bool:
        return self.database.fetch_one("SELECT 1 FROM geheim WHERE sleutel=?", (key,)) is not None

    def delete(self, key: str) -> None:
        self.database.execute("DELETE FROM geheim WHERE sleutel=?", (key,))
