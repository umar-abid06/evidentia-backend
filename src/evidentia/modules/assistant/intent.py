"""Rule-based intent router: cheap, explainable and identical every time."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from evidentia.modules.assistant.concepts import detect_topics

IntentKind = Literal[
    "lookup", "explain", "compare_specific", "compare_general", "summary", "follow_up"
]

_COMPARE = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference|differences|better|best|"
    r"which (card|policy|one)|side by side)\b"
)
_SUMMARY = re.compile(
    r"\b(summar(y|ise|ize)|overview|everything|"
    r"all (of )?(my )?(coverages?|benefits|cover)|what am i covered for|"
    r"what (does|do) my (cards?|polic(y|ies)) cover)\b"
)
_EXPLAIN = re.compile(
    r"\b(how (does|do|is|are|can|would)|explain|what happens|why|process|steps)\b"
)
_INSURANCE_WORDS = re.compile(
    r"\b(cover(ed|ages?)?|insur(ed|ance)|polic(y|ies)|benefits?|limits?|cards?|claims?|excess|"
    r"deductible|travel|trip|abroad|refund|reimburs\w*)\b"
)
_PRONOUNS = {"it", "that", "this", "those", "them", "they", "one"}
_SHORT_REPLIES = {
    "yes",
    "yeah",
    "yep",
    "ok",
    "okay",
    "sure",
    "more",
    "tell me more",
    "go on",
    "continue",
    "and",
    "what else",
    "explain more",
    "more details",
    "details",
    "why",
    "how",
}
_NORMALIZE = re.compile(r"[^\w\s]")


@dataclass(frozen=True, slots=True)
class Intent:
    kind: IntentKind
    topics: list[str] = field(default_factory=list)
    off_topic: bool = False

    @property
    def needs_vector_search(self) -> bool:
        return self.kind in {"lookup", "explain", "compare_specific"}


def normalize_question(question: str) -> str:
    return " ".join(_NORMALIZE.sub(" ", question.lower()).split())


_FOLLOW_UP_PREFIXES = ("tell me more", "more about", "what about it", "and what about")


def is_follow_up(normalized: str, topics: list[str]) -> bool:
    """Short, context-dependent replies that only make sense after a previous answer."""
    if normalized in _SHORT_REPLIES:
        return True
    if topics:
        return False
    words = normalized.split()
    return normalized.startswith(_FOLLOW_UP_PREFIXES) or (
        len(words) <= 5 and bool(_PRONOUNS & set(words))
    )


def classify(question: str) -> Intent:
    text = normalize_question(question)
    topics = detect_topics(text)
    if is_follow_up(text, topics):
        return Intent("follow_up", topics)
    unrelated = not topics and not _INSURANCE_WORDS.search(text)
    if _COMPARE.search(text):
        kind: IntentKind = "compare_specific" if topics else "compare_general"
        return Intent(kind, topics, off_topic=unrelated)
    if _SUMMARY.search(text):
        return Intent("summary", topics)
    kind = "explain" if _EXPLAIN.search(text) else "lookup"
    return Intent(kind, topics, off_topic=unrelated)
