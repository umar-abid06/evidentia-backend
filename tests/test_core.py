from __future__ import annotations

from fastapi import FastAPI
from httpx import AsyncClient

from evidentia.core.errors import NotFoundError
from evidentia.db.base import uuid7


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_reports_each_check(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


async def test_readiness_degrades_when_a_dependency_fails(
    app: FastAPI, client: AsyncClient
) -> None:
    class Broken:
        async def ping(self) -> None:
            raise ConnectionError

    app.state.db = Broken()
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "error: ConnectionError"


async def test_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"x-request-id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


async def test_app_errors_render_problem_details(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise NotFoundError("Nothing here", code="thing_missing")

    response = await client.get("/boom")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "thing_missing"
    assert body["detail"] == "Nothing here"
    assert body["request_id"]


def test_uuid7_is_time_ordered_and_versioned() -> None:
    first, second = uuid7(), uuid7()
    assert first.version == 7
    assert first.int >> 80 <= second.int >> 80
