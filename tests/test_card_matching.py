from __future__ import annotations

import httpx
import pytest

from evidentia.core.config import CardsSettings
from evidentia.integrations.bin_lookup import (
    BinLookupUnavailableError,
    HttpBinLookup,
    StaticBinLookup,
)
from evidentia.modules.cards.matching import (
    Scored,
    decide,
    issuer_score,
    issuer_tokens,
    product_score,
    product_variants,
)


def test_issuer_names_are_normalised() -> None:
    assert issuer_tokens("Jyske Bank A/S (Denmark)") == ["jyske"]
    assert issuer_tokens("The Royal Bank of Scotland PLC") == ["royal", "scotland"]
    assert issuer_tokens("Example Holdings GmbH & Co. KG") == ["example"]


def test_issuers_without_a_shared_distinctive_word_never_match() -> None:
    assert issuer_score("Bank of Alpha", "Bank of Beta") == 0.0
    assert issuer_score("Commerz Bank AG", "Commerzbank") == 0.0  # different tokens
    assert issuer_score("Jyske Bank A/S", "JYSKE BANK") == 100.0


def test_co_brands_are_split_and_scheme_words_stripped() -> None:
    assert product_variants("Miles & More / Gold Mastercard") == [
        ["miles", "more", "gold"],
        ["miles"],
        ["more"],
        ["gold"],
    ]
    assert product_variants("Visa Classic") == [["classic"]]


def test_generic_product_names_are_not_distinctive() -> None:
    assert product_score("Visa Classic", "Classic") == 50.0
    assert product_score("Visa Classic", "Platinum") == 0.0
    assert product_score("Lufthansa Miles & More Gold", "Miles & More Gold") == 100.0
    assert product_score("Miles & More Gold Mastercard", "Miles & More Gold") == 100.0


def test_clear_winner_is_accepted() -> None:
    decision = decide([Scored("a", 100, 92), Scored("b", 100, 50)])
    assert decision.outcome == "matched"
    assert decision.winner is not None
    assert decision.winner.product_id == "a"


def test_close_candidates_are_declined_as_ambiguous() -> None:
    decision = decide([Scored("a", 100, 90), Scored("b", 100, 85)])
    assert decision.outcome == "ambiguous"
    assert {s.product_id for s in decision.ranked} == {"a", "b"}


def test_weak_evidence_is_not_found() -> None:
    assert decide([Scored("a", 40, 100)]).outcome == "not_found"
    assert decide([]).outcome == "not_found"


def test_each_score_rule_allows_a_match_below_ninety() -> None:
    decision = decide([Scored("a", 60, 70)])
    assert decision.outcome == "matched"


async def test_http_lookup_parses_binlist_style_payloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/45717360")
        return httpx.Response(
            200,
            json={
                "scheme": "visa",
                "type": "credit",
                "brand": "Visa Gold",
                "country": {"alpha2": "dk"},
                "bank": {"name": "Jyske Bank"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        lookup = HttpBinLookup(client, CardsSettings(bin_lookup_url="https://bins.test/v1"))
        [record] = await lookup.lookup("45717360")
    assert (record.issuer, record.product_name, record.country_code) == (
        "Jyske Bank",
        "Visa Gold",
        "DK",
    )
    assert record.funding_type == "credit"


async def test_http_lookup_reports_busy_service() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as client:
        lookup = HttpBinLookup(client, CardsSettings(bin_lookup_url="https://bins.test"))
        with pytest.raises(BinLookupUnavailableError):
            await lookup.lookup("411111")


async def test_static_lookup_matches_longest_prefix() -> None:
    from evidentia.integrations.bin_lookup import BinRecord

    record = BinRecord("Example Bank", "Gold", "visa", "credit", "DE")
    lookup = StaticBinLookup({"411111": [record]})
    assert await lookup.lookup("41111122") == [record]
    assert await lookup.lookup("999999") == []
