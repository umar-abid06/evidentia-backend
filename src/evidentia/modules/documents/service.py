"""Upload, pre-flight and lifecycle commands for source documents."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.api.pagination import Page, PageParams, paginate
from evidentia.core.config import IngestionSettings, StorageSettings
from evidentia.core.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    TooManyRequestsError,
    UnprocessableError,
)
from evidentia.core.tasks import enqueue_after_commit
from evidentia.integrations.storage import ObjectStorage
from evidentia.modules.auth.models import User
from evidentia.modules.catalog.service import CatalogService
from evidentia.modules.documents.models import (
    QUEUED_STATUSES,
    Document,
    DocumentPage,
    DocumentStatus,
    ReviewStatus,
)
from evidentia.modules.documents.schemas import (
    DocumentFilters,
    SignedUploadRead,
    UploadRequest,
    UploadTicket,
)
from evidentia.workers.names import PROCESS_DOCUMENT


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage,
        *,
        storage_config: StorageSettings,
        ingestion: IngestionSettings,
        provider_configured: bool,
    ) -> None:
        self.session = session
        self.storage = storage
        self.storage_config = storage_config
        self.ingestion = ingestion
        self.provider_configured = provider_configured
        self.catalog = CatalogService(session)

    async def create_upload(self, user: User, request: UploadRequest) -> UploadTicket:
        if request.size_bytes > self.storage_config.max_upload_bytes:
            raise UnprocessableError(
                f"Files are limited to {self.storage_config.max_upload_mb} MB.",
                code="file_too_large",
            )
        await self.catalog.get_policy(request.policy_id)
        duplicate = await self._find_active_duplicate(request.sha256)
        if duplicate is not None:
            raise ConflictError(
                "This exact file has already been uploaded.",
                code="duplicate_document",
                extra={"document_id": str(duplicate.id)},
            )

        document_id = uuid.uuid4()
        document = Document(
            id=document_id,
            policy_id=request.policy_id,
            intent=request.intent,
            original_filename=request.filename,
            content_type=request.content_type,
            size_bytes=request.size_bytes,
            sha256=request.sha256,
            storage_key=f"documents/{request.policy_id}/{document_id}.pdf",
            uploaded_by_id=user.id,
        )
        self.session.add(document)
        await self.session.commit()

        signed = await self.storage.sign_upload(
            document.storage_key,
            content_type=request.content_type,
            max_bytes=self.storage_config.max_upload_bytes,
            expires_s=self.storage_config.presign_ttl_s,
        )
        return UploadTicket(
            document_id=document.id,
            upload=SignedUploadRead(
                url=signed.url, fields=signed.fields, expires_at=signed.expires_at
            ),
        )

    async def complete_upload(self, document_id: uuid.UUID) -> Document:
        document = await self.get(document_id)
        if document.status is not DocumentStatus.UPLOADING:
            raise ConflictError("Upload was already completed.", code="upload_already_completed")
        stored = await self.storage.stat(document.storage_key)
        if stored is None:
            raise UnprocessableError("The file has not reached storage yet.", code="upload_missing")
        if stored.size_bytes != document.size_bytes:
            raise UnprocessableError(
                "Stored file size does not match the declared size.", code="upload_size_mismatch"
            )
        await self.preflight(document)
        self._queue(document)
        await self.session.commit()
        return document

    async def reprocess(self, document_id: uuid.UUID, *, model: str | None) -> Document:
        document = await self.get(document_id)
        if document.status not in {DocumentStatus.COMPLETED, DocumentStatus.FAILED}:
            raise ConflictError(
                "Only completed or failed documents can be reprocessed.", code="not_reprocessable"
            )
        if document.review_status is ReviewStatus.APPROVED:
            raise ConflictError("Approved documents are immutable.", code="already_approved")
        await self.preflight(document)
        document.extraction_model = model
        document.review_status = ReviewStatus.PENDING
        document.error = None
        self._queue(document)
        await self.session.commit()
        return document

    async def preflight(self, document: Document) -> None:
        """Checks that must pass before any model spend."""
        if not await self.catalog.is_policy_live_for_cards(document.policy_id):
            raise UnprocessableError(
                "The policy is inactive or no active card carries it.", code="card_inactive"
            )
        if not self.provider_configured:
            raise ServiceUnavailableError(
                "The model provider is not configured.", code="provider_not_configured"
            )
        queued = await self.session.scalar(
            select(func.count()).select_from(Document).where(Document.status.in_(QUEUED_STATUSES))
        )
        if (queued or 0) >= self.ingestion.max_queue_depth:
            raise TooManyRequestsError(
                f"The processing queue is full ({self.ingestion.max_queue_depth} documents). "
                "Try again shortly.",
                code="queue_full",
            )

    async def get(self, document_id: uuid.UUID) -> Document:
        document = await self.session.get(Document, document_id)
        if document is None:
            raise NotFoundError("Document not found.", code="document_not_found")
        return document

    async def list(self, params: PageParams, filters: DocumentFilters) -> Page[Any]:
        query = select(Document)
        if filters.status:
            query = query.where(Document.status == filters.status)
        if filters.review_status:
            query = query.where(Document.review_status == filters.review_status)
        if filters.policy_id:
            query = query.where(Document.policy_id == filters.policy_id)
        order = Document.created_at.desc() if filters.order == "newest" else Document.created_at
        return await paginate(self.session, query.order_by(order), params)

    async def page(self, document_id: uuid.UUID, page_number: int) -> DocumentPage:
        page = await self.session.scalar(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id, DocumentPage.page_number == page_number
            )
        )
        if page is None:
            raise NotFoundError("Page not found.", code="page_not_found")
        return page

    def _queue(self, document: Document) -> None:
        document.status = DocumentStatus.PENDING
        enqueue_after_commit(self.session, PROCESS_DOCUMENT, str(document.id))

    async def _find_active_duplicate(self, sha256: str) -> Document | None:
        result = await self.session.scalars(
            select(Document)
            .where(
                Document.sha256 == sha256,
                Document.status != DocumentStatus.FAILED,
                Document.review_status != ReviewStatus.REJECTED,
            )
            .limit(1)
        )
        return result.first()
