"""Versioned API router aggregating every module router."""

from __future__ import annotations

from fastapi import APIRouter


def build_api_router(prefix: str) -> APIRouter:
    api = APIRouter(prefix=prefix)
    return api
