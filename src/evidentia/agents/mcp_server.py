"""MCP server: the platform's typed service boundaries, published as agent tools.

Every tool acts on behalf of exactly one customer and goes through the same
scoping, grounding and authorisation code as the public API. Tools never return
facts from policies the customer does not hold.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Receive, Scope, Send

from evidentia import __version__
from evidentia.ai.source import GatewaySource
from evidentia.core.config import Settings
from evidentia.core.errors import AppError
from evidentia.core.i18n import Language
from evidentia.db.session import Database
from evidentia.modules.admin.store import prompt_registry
from evidentia.modules.assistant.engine import AnswerEngine
from evidentia.modules.assistant.retrieval import PgVectorRetriever
from evidentia.modules.assistant.schemas import AskRequest, AskResponse
from evidentia.modules.auth.models import User
from evidentia.modules.cards.schemas import IdentifyResult
from evidentia.modules.cards.scope import ScopedPolicy, resolve_policy_scope
from evidentia.modules.cards.service import CardService
from evidentia.modules.travel.models import GenerationStatus
from evidentia.modules.travel.service import TravelService

INSTRUCTIONS = """\
Grounded coverage knowledge for service agents. Pass the customer's id to every
customer tool; results only ever include that customer's own policies. When a tool
says a topic is not covered or not found, relay that plainly instead of guessing."""


class PolicySummary(BaseModel):
    policy_id: uuid.UUID
    policy_name: str
    card_id: uuid.UUID
    card_name: str


class GuideSummary(BaseModel):
    status: GenerationStatus
    version: int
    content: dict[str, Any] | None


class ServiceTokenMiddleware:
    """Bearer service-token check in front of the MCP transport (constant-time)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            provided = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(provided, self.expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def build_mcp_server(app: FastAPI) -> MCPServer:
    server = MCPServer(name="evidentia", instructions=INSTRUCTIONS, version=__version__)

    @asynccontextmanager
    async def unit_of_work() -> AsyncIterator[AsyncSession]:
        db: Database = app.state.db
        async with db.session() as session:
            yield session

    async def customer(session: AsyncSession, customer_id: uuid.UUID) -> User:
        user = await session.get(User, customer_id)
        if user is None or not user.is_active:
            raise ToolError("Unknown or inactive customer.")
        return user

    async def engine(session: AsyncSession) -> AnswerEngine:
        settings: Settings = app.state.settings
        source: GatewaySource = app.state.ai
        gateway = await source.get(session)

        async def scope(user: User, card_id: uuid.UUID | None) -> list[ScopedPolicy]:
            return await resolve_policy_scope(session, user.id, card_id=card_id)

        retriever = PgVectorRetriever(
            session,
            embedding_model=gateway.embeddings.model,
            embedding_version=settings.llm.embedding_version,
        )
        return AnswerEngine(gateway, prompt_registry(session), retriever, scope, settings.retrieval)

    @server.tool(
        name="lookup_policies",
        description="List the published policies a customer holds, per card.",
    )
    async def lookup_policies(
        customer_id: uuid.UUID, card_id: uuid.UUID | None = None
    ) -> list[PolicySummary]:
        async with unit_of_work() as session:
            user = await customer(session, customer_id)
            scoped = await resolve_policy_scope(session, user.id, card_id=card_id)
            return [
                PolicySummary(
                    policy_id=p.policy_id,
                    policy_name=p.policy_name,
                    card_id=p.card_id,
                    card_name=p.card_name,
                )
                for p in scoped
            ]

    @server.tool(
        name="search_coverage",
        description=(
            "Answer a coverage question from the customer's own policies, with citations. "
            "Says plainly when the policies do not mention the topic."
        ),
    )
    async def search_coverage(
        customer_id: uuid.UUID,
        question: str,
        card_id: uuid.UUID | None = None,
        language: Language = "en",
    ) -> AskResponse:
        async with unit_of_work() as session:
            user = await customer(session, customer_id)
            request = AskRequest(question=question, card_id=card_id, language=language)
            return await (await engine(session)).ask(user, request)

    @server.tool(
        name="compare_policies",
        description="Compare the customer's cards on one topic, or across all categories.",
    )
    async def compare_policies(
        customer_id: uuid.UUID, topic: str | None = None, language: Language = "en"
    ) -> AskResponse:
        question = f"Which card has better {topic} cover?" if topic else "Compare all my policies"
        async with unit_of_work() as session:
            user = await customer(session, customer_id)
            request = AskRequest(question=question, language=language)
            return await (await engine(session)).ask(user, request)

    @server.tool(
        name="identify_card",
        description="Identify which card product (and coverage) a BIN belongs to.",
    )
    async def identify_card(
        customer_id: uuid.UUID, bin: str, country_code: str | None = None
    ) -> IdentifyResult:
        if not bin.isdigit() or not 6 <= len(bin) <= 8:
            raise ToolError("Provide only the first 6-8 digits of the card.")
        async with unit_of_work() as session:
            user = await customer(session, customer_id)
            try:
                return await CardService(session, app.state.bin_lookup).identify(
                    user, bin, country_code
                )
            except AppError as exc:
                raise ToolError(exc.detail) from exc

    @server.tool(
        name="destination_health_guide",
        description="Health guide for a destination (generated once, then shared).",
    )
    async def destination_health_guide(country_code: str, region: str = "") -> GuideSummary:
        async with unit_of_work() as session:
            guide = await TravelService(session).ensure_guide(country_code.upper(), region)
            await session.commit()
            ready = guide.status is GenerationStatus.READY
            return GuideSummary(
                status=guide.status, version=guide.version, content=guide.content if ready else None
            )

    return server


def mount_mcp(app: FastAPI, settings: Settings) -> MCPServer | None:
    """Mounts the MCP transport at ``/mcp/`` when enabled. Returns the server."""
    if not settings.mcp.enabled or settings.mcp.service_token is None:
        return None
    server = build_mcp_server(app)
    transport = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        # Host checks are replaced by the service-token gate below.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    app.mount(
        "/mcp",
        ServiceTokenMiddleware(transport, settings.mcp.service_token.get_secret_value()),
    )
    return server
