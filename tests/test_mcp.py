from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp import Client
from pydantic import SecretStr

from evidentia.agents.mcp_server import build_mcp_server
from evidentia.core.config import LLMSettings, McpSettings, Settings
from evidentia.main import create_app

TOKEN = "t" * 40


class _EmptySession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class _EmptyDatabase:
    @asynccontextmanager
    async def session(self) -> AsyncIterator[_EmptySession]:
        yield _EmptySession()


async def test_tools_are_discoverable_with_typed_inputs(app: FastAPI) -> None:
    async with Client(build_mcp_server(app)) as client:
        listed = await client.list_tools()
    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == {
        "lookup_policies",
        "search_coverage",
        "compare_policies",
        "identify_card",
        "destination_health_guide",
    }
    search = tools["search_coverage"].input_schema
    assert set(search["required"]) == {"customer_id", "question"}
    assert search["properties"]["customer_id"]["format"] == "uuid"


async def test_unknown_customers_get_no_data(app: FastAPI) -> None:
    app.state.db = _EmptyDatabase()
    async with Client(build_mcp_server(app)) as client:
        result = await client.call_tool("lookup_policies", {"customer_id": str(uuid.uuid4())})
    assert result.is_error
    assert "Unknown or inactive customer" in str(result.content)


async def test_card_tool_refuses_full_card_numbers(app: FastAPI) -> None:
    async with Client(build_mcp_server(app)) as client:
        result = await client.call_tool(
            "identify_card", {"customer_id": str(uuid.uuid4()), "bin": "4111111111111111"}
        )
    assert result.is_error


async def test_transport_requires_the_service_token() -> None:
    settings = Settings(
        environment="test",
        log_json=False,
        llm=LLMSettings(provider="fake"),
        mcp=McpSettings(enabled=True, service_token=SecretStr(TOKEN)),
    )
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        missing = await http.post("/mcp/", json={})
        wrong = await http.post("/mcp/", json={}, headers={"Authorization": "Bearer nope"})
    assert missing.status_code == wrong.status_code == 401
    assert app.state.mcp_server is not None


def test_mcp_needs_a_strong_token() -> None:
    with pytest.raises(ValueError, match="SERVICE_TOKEN"):
        Settings(mcp=McpSettings(enabled=True, service_token=SecretStr("short")))


def test_mcp_is_off_by_default(app: FastAPI) -> None:
    assert app.state.mcp_server is None


def test_lifespan_starts_and_stops_cleanly() -> None:
    from starlette.testclient import TestClient

    settings = Settings(
        environment="test",
        log_json=False,
        log_level="CRITICAL",
        llm=LLMSettings(provider="fake"),
        mcp=McpSettings(enabled=True, service_token=SecretStr(TOKEN)),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert app.state.notifications is not None
        assert app.state.ai is not None
