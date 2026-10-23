"""Scripted model behaviour for offline pipeline tests."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from evidentia.ai.gateway import AIGateway
from evidentia.ai.providers.fake import HashEmbeddingModel, ScriptedChatModel
from evidentia.ai.types import ChatRequest
from evidentia.modules.extraction.taxonomy import CATEGORIES, DOMAINS, domain_for

_PAGE = re.compile(r"\[PAGE (\d+)\]")
_LINE_ID = re.compile(r"^(cov_[0-9a-f]+)(?: \[(\w+)\])? (.*)$")


def ev(
    value: Any, page: int, *, confidence: float = 0.95, quote: str | None = None
) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "evidence": [{"page": page, "quote": quote or str(value)}],
    }


def item(
    title: str,
    page: int,
    *,
    amount: str | None = None,
    confidence: float = 0.95,
    **fields: Any,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": ev(title, page),
        "item_type": ev("benefit", page),
        "confidence": confidence,
        "evidence": [{"page": page, "quote": title}],
    }
    if amount is not None:
        data["amount"] = ev(amount, page)
    for name, value in fields.items():
        data[name] = ev(value, page)
    return data


KEYWORDS: list[tuple[str, str]] = [
    ("dental", "dental"),
    ("evacuation", "medical_evacuation"),
    ("medical", "emergency_medical"),
    ("baggage delay", "baggage_delay"),
    ("baggage", "baggage_loss"),
    ("cancellation", "trip_cancellation"),
    ("delay", "travel_delay"),
    ("liability", "personal_liability"),
    ("rental", "rental_vehicle"),
    ("purchase", "purchase_protection"),
    ("war", "general_exclusions"),
]


def classify_line(text: str) -> str:
    lowered = text.lower()
    return next((category for word, category in KEYWORDS if word in lowered), "general_conditions")


class PolicyReader:
    """Answers discovery/classify/organize requests like a well-behaved model."""

    def __init__(
        self,
        pages: dict[int, list[dict[str, Any]]],
        *,
        general: dict[str, Any] | None = None,
    ) -> None:
        self.pages = pages
        self.general = (
            general
            if general is not None
            else {
                "insurer_name": ev("Example Insurance SE", 1),
                "policy_name": ev("Gold Travel Cover", 1),
            }
        )

    def discovery(self, request: ChatRequest) -> dict[str, Any]:
        numbers = [int(n) for n in _PAGE.findall(request.messages[-1].content)]
        items = [entry for number in numbers for entry in self.pages.get(number, [])]
        return {"general": self.general if 1 in numbers else {}, "items": items}

    @staticmethod
    def classify(request: ChatRequest) -> dict[str, Any]:
        out = []
        for line in request.messages[-1].content.splitlines():
            match = _LINE_ID.match(line)
            if match:
                out.append(
                    {
                        "item_id": match.group(1),
                        "category": classify_line(match.group(3)),
                        "confidence": 0.93,
                    }
                )
        return {"classifications": out}

    @staticmethod
    def organize(request: ChatRequest) -> dict[str, Any]:
        grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for line in request.messages[-1].content.splitlines():
            match = _LINE_ID.match(line)
            if match:
                category = match.group(2)
                grouped[domain_for(category)][category].append(match.group(1))
        return {
            "domains": [
                {
                    "key": domain,
                    "name": DOMAINS[domain],
                    "summary": f"{DOMAINS[domain]} cover.",
                    "comparison_signals": ["limit"],
                    "subdomains": [
                        {"key": cat, "name": CATEGORIES[cat].label, "item_ids": ids}
                        for cat, ids in categories.items()
                    ],
                }
                for domain, categories in grouped.items()
            ]
        }

    def gateway(self) -> tuple[AIGateway, ScriptedChatModel]:
        chat = ScriptedChatModel(
            {"discovery": self.discovery, "classify": self.classify, "organize": self.organize}
        )
        return AIGateway(
            reasoning=chat, fast=chat, embeddings=HashEmbeddingModel(dimensions=64)
        ), chat
