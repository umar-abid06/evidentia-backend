from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from evidentia.modules.documents.models import DocumentIntent, DocumentStatus, ReviewStatus

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$", to_lower=True)]


class UploadRequest(BaseModel):
    policy_id: uuid.UUID
    intent: DocumentIntent
    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal["application/pdf"] = "application/pdf"
    size_bytes: int = Field(gt=0)
    sha256: Sha256Hex = Field(description="Client-side SHA-256 of the file (fingerprint).")


class SignedUploadRead(BaseModel):
    url: str
    fields: dict[str, str]
    expires_at: datetime


class UploadTicket(BaseModel):
    document_id: uuid.UUID
    upload: SignedUploadRead


class ReprocessRequest(BaseModel):
    model: str | None = Field(
        default=None, max_length=100, description="Extraction model override for this run."
    )


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    policy_id: uuid.UUID
    intent: DocumentIntent
    status: DocumentStatus
    review_status: ReviewStatus
    original_filename: str
    size_bytes: int
    sha256: str
    page_count: int | None
    needs_ocr: bool
    overall_confidence: float | None
    review_reasons: list[Any]
    attempts: int
    error: dict[str, Any] | None
    created_at: datetime
    processed_at: datetime | None


class DocumentDetail(DocumentRead):
    extraction_model: str | None
    extraction: dict[str, Any] | None
    conflicts: list[Any]


class PageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    text: str
    char_count: int


class DocumentFilters(BaseModel):
    status: DocumentStatus | None = None
    review_status: ReviewStatus | None = None
    policy_id: uuid.UUID | None = None
    order: Literal["newest", "oldest"] = "newest"
