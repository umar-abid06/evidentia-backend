"""Review gate: only clean extractions publish automatically."""

from __future__ import annotations

from pydantic import BaseModel, Field

from evidentia.modules.extraction.schemas import Conflict, CoverageRecord

MANDATORY_GENERAL = ("insurer_name", "policy_name")


class ReviewReason(BaseModel):
    code: str
    message: str
    items: list[str] = Field(default_factory=list)


def overall_confidence(record: CoverageRecord) -> float:
    if not record.coverages:
        return 0.0
    return round(sum(entry.confidence for entry in record.coverages) / len(record.coverages), 4)


def review_gate(
    record: CoverageRecord,
    *,
    conflicts: list[Conflict],
    unplaced: list[str],
    dropped: int,
    threshold: float,
    sparse: bool,
) -> tuple[float, list[ReviewReason]]:
    confidence = overall_confidence(record)
    reasons: list[ReviewReason] = []
    if not record.coverages:
        reasons.append(ReviewReason(code="missing_coverage", message="No coverage was extracted."))
    if record.coverages and confidence < threshold:
        reasons.append(
            ReviewReason(
                code="low_confidence",
                message=f"Overall confidence {confidence:.2f} is below {threshold:.2f}.",
                items=[e.id for e in record.coverages if e.confidence < threshold],
            )
        )
    if conflicts:
        reasons.append(
            ReviewReason(
                code="conflicting_values",
                message=f"{len(conflicts)} field(s) disagree across reading chunks.",
                items=sorted({conflict.item_id for conflict in conflicts}),
            )
        )
    if unplaced:
        reasons.append(
            ReviewReason(
                code="unplaced_items",
                message=f"{len(unplaced)} item(s) could not be classified or organised.",
                items=sorted(unplaced),
            )
        )
    if dropped:
        reasons.append(
            ReviewReason(
                code="unsupported_items",
                message=f"{dropped} candidate item(s) had no title or evidence and were dropped.",
            )
        )
    if not sparse:
        missing = [name for name in MANDATORY_GENERAL if name not in record.general]
        if missing:
            reasons.append(
                ReviewReason(
                    code="missing_general_data",
                    message="General policy data is incomplete.",
                    items=missing,
                )
            )
    return confidence, reasons
