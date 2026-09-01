"""Vastleggen, herstellen en teruglezen van verplichte bestandsmetadata."""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MetadataError(RuntimeError):
    """Metadata kon niet volledig worden gelezen, toegepast of gecontroleerd."""


@dataclass(frozen=True, slots=True)
class FileMetadata:
    uid: int
    gid: int
    mode: int
    acl: str
    xattrs: dict[str, str]

    def to_json(self) -> str:
        return json.dumps(
            {
                "uid": self.uid,
                "gid": self.gid,
                "mode": self.mode,
                "acl": self.acl,
                "xattrs": self.xattrs,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, value: str) -> FileMetadata:
        raw: Any = json.loads(value)
        if not isinstance(raw, dict) or not isinstance(raw.get("xattrs"), dict):
            raise MetadataError("Ongeldige opgeslagen bestandsmetadata")
        return cls(
            uid=int(raw["uid"]),
            gid=int(raw["gid"]),
            mode=int(raw["mode"]),
            acl=str(raw["acl"]),
            xattrs={str(key): str(item) for key, item in raw["xattrs"].items()},
        )


def _acl(path: Path) -> str:
    result = subprocess.run(
        ["getfacl", "--absolute-names", "--numeric", "--omit-header", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise MetadataError(f"POSIX-ACL lezen mislukt: {result.stderr.strip()}")
    return result.stdout.strip()


def capture_metadata(path: Path) -> FileMetadata:
    try:
        file_stat = path.stat()
        attributes: dict[str, str] = {}
        for name in os.listxattr(path, follow_symlinks=False):
            # POSIX-ACL's worden via getfacl/setfacl behandeld; dubbel zetten
            # kan op sommige filesystems de ACL-maskerbits veranderen.
            if name.startswith("system.posix_acl_"):
                continue
            value = os.getxattr(path, name, follow_symlinks=False)
            attributes[name] = base64.b64encode(value).decode("ascii")
    except OSError as exc:
        raise MetadataError(f"Bestandsmetadata lezen mislukt: {exc}") from exc
    return FileMetadata(
        uid=file_stat.st_uid,
        gid=file_stat.st_gid,
        mode=stat.S_IMODE(file_stat.st_mode),
        acl=_acl(path),
        xattrs=attributes,
    )


def apply_metadata(path: Path, metadata: FileMetadata) -> None:
    try:
        current_stat = path.stat()
        if (current_stat.st_uid, current_stat.st_gid) != (metadata.uid, metadata.gid):
            os.chown(path, metadata.uid, metadata.gid, follow_symlinks=False)
        if stat.S_IMODE(current_stat.st_mode) != metadata.mode:
            os.chmod(path, metadata.mode, follow_symlinks=False)
    except OSError as exc:
        raise MetadataError(f"Eigenaar of mode herstellen mislukt: {exc}") from exc

    # CIFS kan een vaste, synthetische ACL presenteren maar geen setfacl
    # ondersteunen. Als die zichtbare ACL al exact klopt, hoeft er niets te
    # worden gezet; iedere echte afwijking blijft hard falen.
    if _acl(path) != metadata.acl:
        acl_result = subprocess.run(
            ["setfacl", "--set-file=-", str(path)],
            input=metadata.acl + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if acl_result.returncode != 0:
            raise MetadataError(f"POSIX-ACL herstellen mislukt: {acl_result.stderr.strip()}")
    try:
        current_names = {
            name
            for name in os.listxattr(path, follow_symlinks=False)
            if not name.startswith("system.posix_acl_")
        }
        for name in current_names - metadata.xattrs.keys():
            os.removexattr(path, name, follow_symlinks=False)
        for name, encoded in metadata.xattrs.items():
            os.setxattr(
                path,
                name,
                base64.b64decode(encoded, validate=True),
                follow_symlinks=False,
            )
    except (OSError, ValueError) as exc:
        raise MetadataError(f"Extended attributes herstellen mislukt: {exc}") from exc

    restored = capture_metadata(path)
    if restored != metadata:
        raise MetadataError("Teruggelezen eigenaar, mode, ACL of xattrs wijken af")
