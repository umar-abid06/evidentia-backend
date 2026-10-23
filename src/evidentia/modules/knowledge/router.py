from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from evidentia.api.deps import SessionDep
from evidentia.api.pagination import Page, PageDep
from evidentia.modules.auth.dependencies import AdminUser, StaffUser
from evidentia.modules.documents.models import Document
from evidentia.modules.documents.schemas import DocumentRead
from evidentia.modules.knowledge.models import PolicyVersion
from evidentia.modules.knowledge.schemas import (
    ApprovalResult,
    ChangeRead,
    RejectRequest,
    VersionDetail,
    VersionRead,
)
from evidentia.modules.knowledge.service import KnowledgeService


def get_knowledge_service(session: SessionDep) -> KnowledgeService:
    return KnowledgeService(session)


KnowledgeDep = Annotated[KnowledgeService, Depends(get_knowledge_service)]

router = APIRouter(prefix="/admin", tags=["admin: knowledge"])


@router.post("/documents/{document_id}/approve", response_model=ApprovalResult)
async def approve_document(
    document_id: uuid.UUID, user: StaffUser, service: KnowledgeDep
) -> ApprovalResult:
    """Approves the extraction and stages a new version for publishing."""
    return await service.approve_document(document_id, actor_id=user.id)


@router.post("/documents/{document_id}/reject", response_model=DocumentRead)
async def reject_document(
    document_id: uuid.UUID, payload: RejectRequest, user: StaffUser, service: KnowledgeDep
) -> Document:
    return await service.reject_document(document_id, actor_id=user.id, reason=payload.reason)


@router.get("/policies/{policy_id}/versions", response_model=Page[VersionRead])
async def list_versions(
    policy_id: uuid.UUID, _: StaffUser, service: KnowledgeDep, page: PageDep
) -> Page[Any]:
    return await service.list_versions(policy_id, page)


@router.get("/policies/{policy_id}/versions/{version_id}", response_model=VersionDetail)
async def get_version(
    policy_id: uuid.UUID, version_id: uuid.UUID, _: StaffUser, service: KnowledgeDep
) -> PolicyVersion:
    return await service.get_version(policy_id, version_id)


@router.post(
    "/policies/{policy_id}/versions/{version_id}/publish",
    response_model=VersionRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_publish(
    policy_id: uuid.UUID, version_id: uuid.UUID, _: AdminUser, service: KnowledgeDep
) -> PolicyVersion:
    """Resumes a failed publish exactly where it stopped."""
    return await service.republish(policy_id, version_id)


@router.get("/policies/{policy_id}/changes", response_model=Page[ChangeRead])
async def list_changes(
    policy_id: uuid.UUID, _: StaffUser, service: KnowledgeDep, page: PageDep
) -> Page[Any]:
    """Field-level change history: path, action, old value and new value."""
    return await service.changes(policy_id, page)
