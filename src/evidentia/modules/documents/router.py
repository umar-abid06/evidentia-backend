from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, status

from evidentia.api.deps import AIGatewayDep, SessionDep, SettingsDep, StorageDep
from evidentia.api.pagination import Page, PageDep
from evidentia.modules.auth.dependencies import AdminUser, StaffUser
from evidentia.modules.documents.models import Document, DocumentPage
from evidentia.modules.documents.schemas import (
    DocumentDetail,
    DocumentFilters,
    DocumentRead,
    PageRead,
    ReprocessRequest,
    UploadRequest,
    UploadTicket,
)
from evidentia.modules.documents.service import DocumentService


def get_document_service(
    session: SessionDep, storage: StorageDep, settings: SettingsDep, ai: AIGatewayDep
) -> DocumentService:
    return DocumentService(
        session,
        storage,
        storage_config=settings.storage,
        ingestion=settings.ingestion,
        provider_configured=ai.is_configured,
    )


DocumentsDep = Annotated[DocumentService, Depends(get_document_service)]

router = APIRouter(prefix="/admin/documents", tags=["admin: documents"])


@router.post("/uploads", response_model=UploadTicket, status_code=status.HTTP_201_CREATED)
async def create_upload(
    payload: UploadRequest, user: AdminUser, service: DocumentsDep
) -> UploadTicket:
    """Registers an upload and returns a short-lived signed POST to object storage."""
    return await service.create_upload(user, payload)


@router.post(
    "/{document_id}/complete",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_upload(document_id: uuid.UUID, _: AdminUser, service: DocumentsDep) -> Document:
    """Confirms the file reached storage, runs pre-flight checks and queues processing."""
    return await service.complete_upload(document_id)


@router.post(
    "/{document_id}/reprocess",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess(
    document_id: uuid.UUID, payload: ReprocessRequest, _: AdminUser, service: DocumentsDep
) -> Document:
    return await service.reprocess(document_id, model=payload.model)


@router.get("", response_model=Page[DocumentRead])
async def list_documents(
    _: StaffUser,
    service: DocumentsDep,
    page: PageDep,
    filters: Annotated[DocumentFilters, Depends()],
) -> Page[Any]:
    return await service.list(page, filters)


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuid.UUID, _: StaffUser, service: DocumentsDep) -> Document:
    return await service.get(document_id)


@router.get("/{document_id}/pages/{page_number}", response_model=PageRead)
async def get_page(
    document_id: uuid.UUID,
    page_number: Annotated[int, Path(ge=1)],
    _: StaffUser,
    service: DocumentsDep,
) -> DocumentPage:
    return await service.page(document_id, page_number)
