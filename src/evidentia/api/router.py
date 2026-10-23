"""Versioned API router aggregating every module router."""

from __future__ import annotations

from fastapi import APIRouter

from evidentia.modules.auth.router import router as auth_router
from evidentia.modules.catalog.router import router as catalog_router
from evidentia.modules.documents.router import router as documents_router
from evidentia.modules.knowledge.router import router as knowledge_router


def build_api_router(prefix: str) -> APIRouter:
    api = APIRouter(prefix=prefix)
    api.include_router(auth_router)
    api.include_router(catalog_router)
    api.include_router(documents_router)
    api.include_router(knowledge_router)
    return api
