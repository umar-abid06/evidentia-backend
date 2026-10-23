"""Bounded agentic answering.

A planner (deterministic) picks a strategy per question, specialised steps carry it
out, and a single generation call writes the answer. Every decision is traced.

    scope -> translate in -> route intent -> retrieve -> evidence check
          -> layout contract -> one answer call -> localise out
"""

from __future__ import annotations

import json
import uuid
from functools import cache
from importlib import resources
from typing import Protocol

from evidentia.ai.gateway import AIGateway
from evidentia.ai.prompts import PromptRegistry
from evidentia.core.config import RetrievalSettings
from evidentia.core.i18n import Language, as_language
from evidentia.modules.assistant.concepts import (
    CONCEPTS_BY_KEY,
    expansion_terms,
    topic_categories,
)
from evidentia.modules.assistant.intent import Intent, classify
from evidentia.modules.assistant.localization import localize_answer, question_to_english
from evidentia.modules.assistant.prompting import ASSISTANT, build_answer_messages, choose_layout
from evidentia.modules.assistant.ranking import (
    Selection,
    SelectionLimits,
    select_representatives,
    select_semantic,
)
from evidentia.modules.assistant.retrieval import Retriever
from evidentia.modules.assistant.schemas import (
    AnswerStatus,
    AskRequest,
    AskResponse,
    Citation,
    DecisionTrace,
    TraceChunk,
)
from evidentia.modules.auth.models import User
from evidentia.modules.cards.scope import ScopedPolicy

CLARIFY_EXAMPLES = ("medical treatment", "trip cancellation", "baggage delay")


class ScopeResolver(Protocol):
    async def __call__(self, user: User, card_id: uuid.UUID | None) -> list[ScopedPolicy]: ...


@cache
def _messages() -> dict[str, dict[str, str]]:
    text = resources.files(__package__).joinpath("messages.json").read_text("utf-8")
    loaded: dict[str, dict[str, str]] = json.loads(text)
    return loaded


def fixed_reply(language: Language, key: str) -> str:
    table = _messages()
    return table.get(language, {}).get(key) or table["en"][key]


class AnswerEngine:
    def __init__(
        self,
        gateway: AIGateway,
        registry: PromptRegistry,
        retriever: Retriever,
        scope: ScopeResolver,
        settings: RetrievalSettings,
    ) -> None:
        self.gateway = gateway
        self.registry = registry
        self.retriever = retriever
        self.scope = scope
        self.settings = settings
        self.limits = SelectionLimits(
            floor=settings.similarity_floor,
            relaxed_floor=settings.relaxed_floor,
            per_policy=settings.max_chunks_per_policy,
            total=settings.max_chunks_total,
        )

    async def ask(self, user: User, request: AskRequest) -> AskResponse:
        language = request.language or as_language(user.preferred_language)
        include_trace = request.diagnostic and user.is_staff

        scope = await self.scope(user, request.card_id)
        if not scope:
            return self._reply("no_policies", language)
        names = {str(policy.policy_id): policy.policy_name for policy in scope}

        question = await question_to_english(self.gateway, request.question, language)
        intent = classify(question)
        trace = DecisionTrace(
            intent=intent.kind,
            topics=intent.topics,
            expansion_terms=[],
            strategy="none",
            policies=list(names.values()),
        )

        if intent.kind == "follow_up":
            clarify = (
                fixed_reply("en", "clarify_examples").format(examples=", ".join(CLARIFY_EXAMPLES))
                if language == "en"
                else fixed_reply(language, "clarify")
            )
            return self._reply(
                "clarify", language, text=clarify, trace=trace if include_trace else None
            )
        if intent.off_topic:
            return self._reply("off_topic", language, trace=trace if include_trace else None)

        selection = await self._retrieve(intent, question, scope, trace)
        if not selection.chunks:
            specific: str | None = None
            if language == "en" and intent.topics:
                label = CONCEPTS_BY_KEY[intent.topics[0]].label
                specific = fixed_reply("en", "not_covered_topic").format(topic=label)
            return self._reply(
                "not_covered", language, text=specific, trace=trace if include_trace else None
            )

        layout = choose_layout(intent.kind, len(scope))
        prompt = await self.registry.resolve(ASSISTANT)
        messages = build_answer_messages(
            prompt, layout=layout, question=question, chunks=selection.chunks, policy_names=names
        )
        answer = await self.gateway.complete_text(stage="answer", messages=messages, tier="fast")
        protected = sorted(set(names.values()) | {policy.card_name for policy in scope})
        text, localized = await localize_answer(
            self.gateway, answer.strip(), language, protected=protected
        )

        trace.layout = layout
        trace.prompt_version = prompt.version
        return AskResponse(
            status="answered",
            answer=text,
            language=language if localized else "en",
            localized=localized,
            layout=layout,
            policies=list(names.values()),
            citations=self._citations(selection, names),
            trace=trace if include_trace else None,
        )

    async def _retrieve(
        self, intent: Intent, question: str, scope: list[ScopedPolicy], trace: DecisionTrace
    ) -> Selection:
        version_ids = [policy.version_id for policy in scope]
        names = {str(policy.policy_id): policy.policy_name for policy in scope}
        if intent.needs_vector_search:
            terms = expansion_terms(intent.topics, limit=self.settings.max_expansion_terms)
            query = f"{question}\nRelated terms: {', '.join(terms)}" if terms else question
            embedded = await self.gateway.embed(stage="embed_query", texts=[query])
            per_policy = min(
                self.settings.candidates_per_policy,
                max(1, self.settings.max_candidates // len(version_ids)),
            )
            candidates = await self.retriever.semantic(
                version_ids, embedded.vectors[0], per_policy=per_policy
            )
            selection = select_semantic(
                candidates,
                intent=intent.kind,
                topic_categories=topic_categories(intent.topics),
                topics_detected=bool(intent.topics),
                limits=self.limits,
            )
            trace.strategy = "semantic"
            trace.expansion_terms = terms
        else:
            published = await self.retriever.published(version_ids)
            selection = select_representatives(
                published,
                limits=self.limits,
                customer_facing_only=intent.kind == "compare_general",
            )
            trace.strategy = "summary"
        trace.candidates = selection.considered
        trace.strong_candidates = selection.strong_candidates
        trace.floor_applied = selection.floor_applied
        trace.selected = [
            TraceChunk(
                policy_name=names.get(str(chunk.policy_id), "Policy"),
                title=chunk.title,
                category=chunk.category,
                similarity=round(chunk.similarity, 4),
            )
            for chunk in selection.chunks
        ]
        return selection

    @staticmethod
    def _citations(selection: Selection, names: dict[str, str]) -> list[Citation]:
        seen: dict[tuple[str, str], Citation] = {}
        for chunk in selection.chunks:
            key = (names.get(str(chunk.policy_id), "Policy"), chunk.title)
            if key not in seen:
                seen[key] = Citation(
                    policy_name=key[0], title=key[1], pages=list(chunk.fields.get("pages", []))
                )
        return list(seen.values())

    @staticmethod
    def _reply(
        status: AnswerStatus,
        language: Language,
        *,
        text: str | None = None,
        trace: DecisionTrace | None = None,
    ) -> AskResponse:
        return AskResponse(
            status=status,
            answer=text or fixed_reply(language, status),
            language=language,
            localized=language != "en",
            trace=trace,
        )
