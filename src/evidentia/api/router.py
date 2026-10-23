"""Versioned API router aggregating every module router."""

from __future__ import annotations

from fastapi import APIRouter

from evidentia.modules.assistant.router import router as assistant_router
from evidentia.modules.auth.router import router as auth_router
from evidentia.modules.cards.router import admin_router as cards_admin_router
from evidentia.modules.cards.router import router as cards_router
from evidentia.modules.catalog.router import router as catalog_router
from evidentia.modules.documents.router import router as documents_router
from evidentia.modules.knowledge.router import router as knowledge_router


def build_api_router(prefix: str) -> APIRouter:
    api = APIRouter(prefix=prefix)
    api.include_router(auth_router)
    api.include_router(catalog_router)
    api.include_router(documents_router)
    api.include_router(knowledge_router)
    api.include_router(cards_router)
    api.include_router(cards_admin_router)
    api.include_router(assistant_router)
    return api
