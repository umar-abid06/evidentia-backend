from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evidentia.core.i18n import Language
from evidentia.modules.knowledge.models import VersionStatus


class ApprovalResult(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID | None
    unchanged: bool = Field(description="True when the content matched the live version.")


class RejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    policy_id: uuid.UUID
    version_number: int
    status: VersionStatus
    content_hash: str
    source_document_id: uuid.UUID | None
    published_at: datetime | None
    embedding_model: str | None
    embedding_version: str | None
    chunk_count: int
    token_count: int
    embedding_calls: int
    publish_duration_ms: int | None
    error: dict[str, Any] | None
    created_at: datetime


class VersionDetail(VersionRead):
    record: dict[str, Any]


class ChangeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    policy_version_id: uuid.UUID
    document_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    field_path: str
    action: str
    old_value: Any
    new_value: Any
    created_at: datetime


class CoverageView(BaseModel):
    version_id: uuid.UUID
    language: Language
    status: Literal["original", "translated", "pending"] = Field(
        description="'pending' means the translation is being generated; English is served."
    )
    coverage: dict[str, Any]
