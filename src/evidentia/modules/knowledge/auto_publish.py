"""Post-extraction gate: clean results publish automatically, the rest wait for review."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.modules.documents.models import Document, DocumentIntent, ReviewStatus
from evidentia.modules.extraction.amendments import reconcile
from evidentia.modules.extraction.pipeline import ExtractionOutcome
from evidentia.modules.extraction.schemas import CoverageRecord
from evidentia.modules.knowledge.service import KnowledgeService


class AutoPublisher:
    def __init__(self, session: AsyncSession) -> None:
        self.knowledge = KnowledgeService(session)

    async def __call__(self, document: Document, outcome: ExtractionOutcome) -> None:
        reasons: list[dict[str, Any]] = list(document.review_reasons)
        if document.intent is DocumentIntent.AMENDMENT:
            reasons.extend(await self._amendment_checks(document, outcome))
        document.review_reasons = reasons
        if reasons:
            document.review_status = ReviewStatus.NEEDS_REVIEW
            return
        await self.knowledge.approve(document, actor_id=None)

    async def _amendment_checks(
        self, document: Document, outcome: ExtractionOutcome
    ) -> list[dict[str, Any]]:
        live = await self.knowledge.live_version(document.policy_id)
        if live is None:
            return [
                {"code": "no_base_version", "message": "No live version to amend.", "items": []}
            ]
        result = reconcile(CoverageRecord.model_validate(live.record), outcome.record)
        document.report = {
            **(document.report or {}),
            "reconciliation": {
                "base_version_id": str(live.id),
                "changes": len(result.changes),
                "ambiguous": result.ambiguous,
            },
        }
        found: list[dict[str, Any]] = []
        if not result.changes:
            found.append(
                {
                    "code": "amendment_changes_nothing",
                    "message": "The amendment does not change the live policy.",
                    "items": [],
                }
            )
        if result.ambiguous:
            found.append(
                {
                    "code": "ambiguous_matches",
                    "message": "Some changes match several base items; pick the target manually.",
                    "items": result.ambiguous,
                }
            )
        return found
