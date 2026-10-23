"""Liveness and readiness probes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from evidentia import __version__

ReadinessCheck = Callable[[Request], Awaitable[None]]

router = APIRouter(tags=["health"])


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"
    version: str = __version__


class Readiness(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, str]


async def _database_check(request: Request) -> None:
    await request.app.state.db.ping()


async def _redis_check(request: Request) -> None:
    await request.app.state.redis.ping()


READINESS_CHECKS: dict[str, ReadinessCheck] = {
    "database": _database_check,
    "redis": _redis_check,
}


@router.get("/healthz", response_model=Liveness)
async def liveness() -> Liveness:
    return Liveness()


@router.get("/readyz", response_model=Readiness)
async def readiness(request: Request, response: Response) -> Readiness:
    async def run(check: ReadinessCheck) -> str:
        try:
            await asyncio.wait_for(check(request), timeout=2.0)
        except Exception as exc:  # noqa: BLE001 - surfaced as a probe result
            return f"error: {type(exc).__name__}"
        return "ok"

    names = list(READINESS_CHECKS)
    results = await asyncio.gather(*(run(READINESS_CHECKS[name]) for name in names))
    checks = dict(zip(names, results, strict=True))
    ready = all(result == "ok" for result in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ready" if ready else "degraded", checks=checks)
