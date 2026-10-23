"""Versioned API router aggregating every module router."""

from __future__ import annotations

from fastapi import APIRouter

from evidentia.modules.auth.router import router as auth_router


def build_api_router(prefix: str) -> APIRouter:
    api = APIRouter(prefix=prefix)
    api.include_router(auth_router)
    return api
