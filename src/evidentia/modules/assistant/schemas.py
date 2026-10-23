from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from evidentia.core.i18n import Language
from evidentia.modules.assistant.intent import IntentKind
from evidentia.modules.assistant.prompting import Layout

AnswerStatus = Literal["answered", "clarify", "not_covered", "off_topic", "no_policies"]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    card_id: uuid.UUID | None = Field(
        default=None, description="Ask about one card; omit to consult every active card."
    )
    language: Language | None = Field(
        default=None, description="Answer language; defaults to the profile language."
    )
    diagnostic: bool = Field(default=False, description="Staff only: include the decision trace.")


class Citation(BaseModel):
    policy_name: str
    title: str
    pages: list[int]


class TraceChunk(BaseModel):
    policy_name: str
    title: str
    category: str
    similarity: float


class DecisionTrace(BaseModel):
    intent: IntentKind
    topics: list[str]
    expansion_terms: list[str]
    strategy: Literal["semantic", "summary", "none"]
    policies: list[str]
    candidates: int = 0
    strong_candidates: int = 0
    floor_applied: float | None = None
    selected: list[TraceChunk] = Field(default_factory=list)
    layout: Layout | None = None
    prompt_version: str | None = None


class AskResponse(BaseModel):
    status: AnswerStatus
    answer: str
    language: Language
    localized: bool = False
    layout: Layout | None = None
    policies: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    trace: DecisionTrace | None = None
