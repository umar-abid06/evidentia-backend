"""Deterministic card-to-product matching. When the evidence is ambiguous, decline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz

LEGAL_SUFFIXES = frozenset(
    {
        "ab",
        "ag",
        "as",
        "asa",
        "bv",
        "co",
        "company",
        "corp",
        "corporation",
        "gmbh",
        "inc",
        "incorporated",
        "kg",
        "limited",
        "llc",
        "llp",
        "lp",
        "ltd",
        "nv",
        "oy",
        "oyj",
        "pjsc",
        "plc",
        "pvt",
        "sa",
        "sarl",
        "sas",
        "spa",
        "srl",
    }
)
FILLER_WORDS = frozenset(
    {"bank", "banking", "group", "holding", "holdings", "the", "of", "and", "financial", "services"}
)
SCHEME_WORDS = frozenset(
    {
        "visa",
        "mastercard",
        "master",
        "amex",
        "american",
        "express",
        "discover",
        "diners",
        "club",
        "jcb",
        "unionpay",
        "maestro",
        "rupay",
        "card",
    }
)
GENERIC_PRODUCT_WORDS = frozenset(
    {
        "black",
        "business",
        "classic",
        "corporate",
        "credit",
        "elite",
        "gold",
        "infinite",
        "platinum",
        "preferred",
        "premier",
        "rewards",
        "select",
        "signature",
        "standard",
        "titanium",
        "world",
    }
)

_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_JOINERS = re.compile(r"(?<=\w)[./'](?=\w)|(?<=\w)\.")  # "A/S" -> "as", "S.A." -> "sa"
_NON_WORD = re.compile(r"[^a-z0-9]+")
_CO_BRAND = re.compile(r"\s*(?:/|&|\+|\bx\b)\s*")


def _tokens(text: str) -> list[str]:
    return [token for token in _NON_WORD.sub(" ", text.lower()).split() if token]


def issuer_tokens(name: str) -> list[str]:
    """Lower case, no bracketed text or punctuation, no legal suffixes or filler words."""
    words = _tokens(_JOINERS.sub("", _BRACKETED.sub(" ", name.lower())))
    return [w for w in words if w not in LEGAL_SUFFIXES and w not in FILLER_WORDS]


def issuer_score(a: str, b: str) -> float:
    left, right = issuer_tokens(a), issuer_tokens(b)
    if not left or not right or not set(left) & set(right):
        return 0.0  # no shared distinctive word: "Bank of A" never matches "Bank of B"
    return float(fuzz.token_sort_ratio(" ".join(left), " ".join(right)))


def product_variants(name: str) -> list[list[str]]:
    """The full name plus each co-brand part ("X / Y", "X & Y"), scheme words removed."""
    variants: list[list[str]] = []
    for part in [name.lower(), *_CO_BRAND.split(name.lower())]:
        tokens = [token for token in _tokens(part) if token not in SCHEME_WORDS]
        if tokens and tokens not in variants:
            variants.append(tokens)
    return variants or [[]]


def is_generic(tokens: list[str]) -> bool:
    return all(token in GENERIC_PRODUCT_WORDS for token in tokens)


def product_score(record_name: str, catalog_name: str) -> float:
    """Best score across co-brand variants; generic names ("Visa Classic") are capped."""
    best = 0.0
    for left in product_variants(record_name):
        for right in product_variants(catalog_name):
            if is_generic(left) or is_generic(right):
                score = 50.0 if left and sorted(left) == sorted(right) else 0.0
            else:
                score = float(fuzz.token_sort_ratio(" ".join(left), " ".join(right)))
            best = max(best, score)
    return best


@dataclass(frozen=True, slots=True)
class Scored:
    product_id: str
    issuer: float
    product: float

    @property
    def average(self) -> float:
        return round((self.issuer + self.product) / 2, 2)


@dataclass(frozen=True, slots=True)
class AcceptanceRules:
    average: float = 90.0
    each: float = 50.0
    lead: float = 10.0

    def accepts(self, scored: Scored) -> bool:
        return scored.average >= self.average or (
            scored.issuer >= self.each and scored.product >= self.each
        )


@dataclass(slots=True)
class Decision:
    outcome: Literal["matched", "ambiguous", "not_found"]
    winner: Scored | None = None
    ranked: list[Scored] = field(default_factory=list)


def decide(candidates: list[Scored], rules: AcceptanceRules | None = None) -> Decision:
    rules = rules or AcceptanceRules()
    ranked = sorted(candidates, key=lambda s: (s.average, s.issuer, s.product), reverse=True)
    accepted = [scored for scored in ranked if rules.accepts(scored)]
    if not accepted:
        return Decision("not_found", ranked=ranked)
    winner = accepted[0]
    runner_up = next((s for s in ranked if s is not winner and s.issuer > 0), None)
    if runner_up is None or winner.average - runner_up.average >= rules.lead:
        return Decision("matched", winner=winner, ranked=ranked)
    plausible = [s for s in ranked if winner.average - s.average < rules.lead and s.issuer > 0]
    return Decision("ambiguous", ranked=plausible)
