"""Background processing of one uploaded document (runs in a worker)."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.integrations.storage import ObjectStorage
from evidentia.modules.documents.models import (
    Document,
    DocumentIntent,
    DocumentPage,
    DocumentStatus,
    ReviewStatus,
)
from evidentia.modules.documents.text import (
    PageText,
    TextExtractor,
    UnreadableDocumentError,
    is_scanned,
)
from evidentia.modules.extraction.pipeline import (
    ExtractionMode,
    ExtractionOutcome,
    ExtractionPipeline,
)

AfterExtraction = Callable[[Document, ExtractionOutcome], Awaitable[None]]

log = structlog.get_logger(__name__)


class DocumentRejectedError(Exception):
    """A deterministic reason the document cannot proceed (not a crash)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DocumentProcessor:
    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage,
        extractor: TextExtractor,
        pipeline: ExtractionPipeline | None = None,
        after_extraction: AfterExtraction | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.extractor = extractor
        self.pipeline = pipeline
        self.after_extraction = after_extraction

    async def run(self, document_id: uuid.UUID) -> Document | None:
        document = await self.session.get(Document, document_id)
        if document is None or document.status is not DocumentStatus.PENDING:
            # Redelivered or already handled: processing is idempotent per state.
            log.info("document_skipped", document_id=str(document_id))
            return None

        document.status = DocumentStatus.PROCESSING
        document.processing_started_at = datetime.now(UTC)
        document.attempts += 1
        await self.session.commit()

        try:
            pages = await self.extract_pages(document)
            await self.analyze(document, pages)
        except DocumentRejectedError as exc:
            return await self._fail(document_id, code=exc.code, message=str(exc), stage="ingestion")
        except Exception as exc:
            log.exception("document_processing_failed", document_id=str(document_id))
            return await self._fail(
                document_id,
                code="processing_error",
                message=str(exc) or type(exc).__name__,
                stage=getattr(exc, "stage", None) or "pipeline",
                error_type=type(exc).__name__,
                details=_error_details(exc),
            )
        return document

    async def extract_pages(self, document: Document) -> list[PageText]:
        data = await self.storage.read(document.storage_key)
        if hashlib.sha256(data).hexdigest() != document.sha256:
            raise DocumentRejectedError(
                "fingerprint_mismatch", "Stored file does not match its upload fingerprint."
            )
        try:
            pages = await asyncio.to_thread(self.extractor.extract, data)
        except UnreadableDocumentError as exc:
            raise DocumentRejectedError("unreadable_document", str(exc)) from exc

        await self.session.execute(
            delete(DocumentPage).where(DocumentPage.document_id == document.id)
        )
        self.session.add_all(
            DocumentPage(
                document_id=document.id,
                page_number=page.number,
                text=page.text,
                char_count=len(page.text),
            )
            for page in pages
        )
        document.page_count = len(pages)
        document.needs_ocr = not pages or is_scanned(pages)
        await self.session.commit()
        if document.needs_ocr:
            raise DocumentRejectedError(
                "scanned_document", "No page contains extractable text; OCR is required."
            )
        return pages

    async def analyze(self, document: Document, pages: list[PageText]) -> None:
        if self.pipeline is not None:
            mode: ExtractionMode = (
                "amendment" if document.intent is DocumentIntent.AMENDMENT else "full"
            )
            outcome = await self.pipeline.run(pages, mode=mode, model=document.extraction_model)
            document.extraction = outcome.record.model_dump(mode="json")
            document.conflicts = [
                conflict.model_dump(mode="json") for conflict in outcome.conflicts
            ]
            document.review_reasons = [r.model_dump(mode="json") for r in outcome.review_reasons]
            document.overall_confidence = outcome.overall_confidence
            document.review_status = (
                ReviewStatus.NEEDS_REVIEW if outcome.review_reasons else ReviewStatus.PENDING
            )
            telemetry = self.pipeline.gateway.telemetry.report(pages=len(pages))
            document.report = {
                "mode": mode,
                **outcome.stats,
                "telemetry": telemetry.model_dump(mode="json"),
            }
            if self.after_extraction is not None:
                await self.after_extraction(document, outcome)
        document.status = DocumentStatus.COMPLETED
        document.processed_at = datetime.now(UTC)
        await self.session.commit()

    async def _fail(
        self,
        document_id: uuid.UUID,
        *,
        code: str,
        message: str,
        stage: str,
        error_type: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> Document:
        await self.session.rollback()
        document = await self.session.get(Document, document_id, populate_existing=True)
        if document is None:
            raise LookupError(f"document {document_id} disappeared while processing")
        document.status = DocumentStatus.FAILED
        document.review_status = ReviewStatus.NEEDS_REVIEW
        document.processed_at = datetime.now(UTC)
        document.error = {
            "code": code,
            "message": message[:2000],
            "stage": stage,
            "type": error_type,
            "at": datetime.now(UTC).isoformat(),
            **(details or {}),
        }
        await self.session.commit()
        return document


def _error_details(exc: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for attr in ("preview", "errors", "attempts"):
        value = getattr(exc, attr, None)
        if value:
            details[attr] = value
    return details
