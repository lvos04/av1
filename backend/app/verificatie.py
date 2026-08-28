"""Vergelijk een encoderesultaat uitsluitend met vóór de encode opgeslagen metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.media.beslissen import Besluit
from app.media.probe import probe


@dataclass(frozen=True, slots=True)
class VerificationResult:
    passed: bool
    reason: str
    result_probe: dict[str, Any] | None
    mastering_equal: bool


def verify_result(
    source_probe: dict[str, Any],
    result_path: Path,
    source_size: int,
    decision: Besluit,
) -> VerificationResult:
    result = probe(str(result_path))
    if result is None:
        return VerificationResult(False, "resultaat is niet leesbaar", None, False)
    if bool(source_probe.get("hdr10plus")):
        return VerificationResult(False, "HDR10+ had nooit uitgevoerd mogen worden", result, False)
    mastering_equal = source_probe.get("mastering_display") == result.get(
        "mastering_display"
    ) and source_probe.get("content_light") == result.get("content_light")
    if bool(source_probe.get("is_hdr10")) and (
        not bool(result.get("is_hdr10")) or not mastering_equal
    ):
        return VerificationResult(
            False, "HDR10 mastering display of content light is gewijzigd", result, False
        )
    if int(source_probe.get("dv_profile") or 0) and not int(result.get("dv_profile") or 0):
        return VerificationResult(False, "Dolby Vision-RPU is verdwenen", result, mastering_equal)
    if int(result.get("size") or result_path.stat().st_size) > source_size:
        return VerificationResult(False, "resultaat is groter dan de bron", result, mastering_equal)
    if not result.get("audio"):
        return VerificationResult(False, "resultaat bevat geen audiospoor", result, mastering_equal)
    if decision.audio_houden and len(result["audio"]) != len(decision.audio_houden):
        return VerificationResult(
            False, "aantal bewaarde audiosporen wijkt af", result, mastering_equal
        )
    if decision.attachments_houden and int(result.get("attachments") or 0) < int(
        source_probe.get("attachments") or 0
    ):
        return VerificationResult(
            False, "ondertitellettertypen zijn verdwenen", result, mastering_equal
        )
    return VerificationResult(
        True, "metadata en verplichte sporen zijn behouden", result, mastering_equal
    )
