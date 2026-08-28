"""Zet een opgeslagen scanresultaat om in een zichtbaar, nog niet uitvoerbaar besluit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.media.beslissen import Besluit, beslis
from app.media.bestandsfilter import veilig_om_op_te_ruimen


def decide_catalog_item(
    path: str,
    category: str,
    probe_data: dict[str, Any] | None,
    profile: dict[str, Any],
    nlink: int,
) -> Besluit:
    """Beslis conservatief; deze functie wijzigt geen bestand en plant niets in."""

    if nlink > 1:
        return Besluit(
            actie="overslaan",
            reden=f"hardlink gedetecteerd ({nlink} koppelingen); standaard overslaan",
            waarschuwingen=["HARDLINK GEDETECTEERD"],
        )
    if category == "extra":
        allowed, reason = veilig_om_op_te_ruimen(path, float(profile.get("extra_max_mb", 500)))
        if allowed:
            return Besluit(
                actie="opruimen",
                reden=f"extra-bestand; tweede veiligheidscontrole: {reason}",
            )
        return Besluit(
            actie="overslaan",
            reden=f"leek een extra, maar blijft staan: {reason}",
            waarschuwingen=["leek een extra, maar blijft staan"],
        )
    if category == "kort":
        return Besluit(actie="overslaan", reden="kort bestand wordt nooit opgeruimd")
    if probe_data is None:
        return Besluit(actie="overslaan", reden="media-analyse mislukt; bij twijfel overslaan")
    return beslis(probe_data, profile)


def store_decision(
    connection: Any,
    bestand_id: int,
    decision: Besluit,
    changed: bool,
) -> int | None:
    """Vervang alleen nog niet uitgevoerde besluiten wanneer een bestand veranderde."""

    existing = connection.execute(
        "SELECT id FROM besluit WHERE bestand_id=? AND status='nieuw' ORDER BY id DESC LIMIT 1",
        (bestand_id,),
    ).fetchone()
    if existing is not None and not changed:
        return int(existing["id"])
    if changed:
        connection.execute(
            "DELETE FROM besluit WHERE bestand_id=? AND status IN ('nieuw', 'afgewezen')",
            (bestand_id,),
        )
    cursor = connection.execute(
        """
        INSERT INTO besluit(
            bestand_id, actie, baan, encoder, reden, payload, status, gemaakt_op
        ) VALUES (?, ?, ?, ?, ?, ?, 'nieuw', ?)
        """,
        (
            bestand_id,
            decision.actie,
            decision.baan or None,
            decision.encoder or None,
            decision.reden,
            decision.model_dump_json(),
            datetime.now(UTC).isoformat(),
        ),
    )
    return int(cursor.lastrowid)
