from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from evidentia.ai.errors import ContractViolationError
from evidentia.ai.gateway import AIGateway
from evidentia.ai.prompts import PromptRegistry
from evidentia.ai.providers.fake import HashEmbeddingModel, ScriptedChatModel
from evidentia.ai.types import ChatRequest
from evidentia.modules.knowledge.translation import collect_leaves
from evidentia.modules.travel.generation import (
    GUIDE_TRANSLATABLE_KEYS,
    GuideOutOfScopeError,
    HealthGuideContent,
    PlanDraft,
    assemble_plan,
    generate_health_guide,
    generate_jetlag_plan,
)
from evidentia.modules.travel.jetlag import (
    FlightSegment,
    UnknownAirportError,
    compute,
    normalize_shift,
    recovery_days,
)
from evidentia.modules.travel.schemas import TripCreate


def seg(origin: str, destination: str, dep: str, arr: str) -> FlightSegment:
    return FlightSegment(
        origin=origin,
        destination=destination,
        departure=datetime.fromisoformat(dep),
        arrival=datetime.fromisoformat(arr),
    )


def test_eastbound_shift_and_recovery() -> None:
    metrics = compute([seg("FRA", "HND", "2026-07-01T13:00", "2026-07-02T08:30")])
    assert metrics.total_shift_hours == 7
    assert metrics.direction == "eastbound"
    assert metrics.recovery_days == 7
    assert metrics.segments[0].duration_hours == 12.5
    assert [p.key for p in metrics.phases] == ["pre_departure", "travel", "arrival"]


def test_westbound_recovers_faster() -> None:
    metrics = compute([seg("fra", "jfk", "2026-07-01T10:00", "2026-07-01T12:45")])
    assert metrics.total_shift_hours == -6
    assert metrics.direction == "westbound"
    assert metrics.recovery_days == 4


def test_the_body_clock_takes_the_short_way_round() -> None:
    assert normalize_shift(-19) == 5
    assert normalize_shift(13) == -11
    metrics = compute([seg("NRT", "HNL", "2026-07-01T21:00", "2026-07-01T09:00")])
    assert metrics.direction == "eastbound"


def test_long_stopovers_get_their_own_phase() -> None:
    metrics = compute(
        [
            seg("FRA", "DXB", "2026-07-01T10:00", "2026-07-01T18:00"),
            seg("DXB", "SYD", "2026-07-03T02:00", "2026-07-03T22:00"),
        ]
    )
    assert [s.airport for s in metrics.stopovers] == ["DXB"]
    assert metrics.phases[2].key == "stopover_dxb"
    assert metrics.phases[-1].location == "SYD"


def test_small_shifts_need_little_recovery() -> None:
    assert recovery_days(0.5) == 0
    assert recovery_days(2) == 1
    assert recovery_days(-4.5) == 3


def test_bad_itineraries_are_rejected() -> None:
    with pytest.raises(UnknownAirportError):
        compute([seg("XXX", "FRA", "2026-07-01T10:00", "2026-07-01T12:00")])
    with pytest.raises(ValueError, match="arrives before"):
        compute([seg("FRA", "LHR", "2026-07-01T10:00", "2026-07-01T08:00")])


def _advice() -> dict[str, str]:
    keys = ("sleep", "light", "hydration", "meals", "caffeine", "activity")
    return {key: f"{key} advice" for key in keys}


def _draft_for(metrics: Any) -> dict[str, Any]:
    return {
        "phases": [{"key": p.key, "days": [_advice()] * p.days} for p in metrics.phases],
        "expert_tips": ["Shift meals early."],
        "checklist": ["Eye mask"],
    }


def test_days_and_labels_come_from_the_application() -> None:
    metrics = compute([seg("FRA", "HND", "2026-07-01T13:00", "2026-07-02T08:30")])
    plan = assemble_plan(metrics, PlanDraft.model_validate(_draft_for(metrics)))
    numbers = [day.day for day in plan.days]
    assert numbers[:3] == [-2, -1, 1]
    assert 0 not in numbers
    assert len(plan.days) == 2 + 1 + 7
    assert plan.days[2].label == "Travel day"


def test_plans_with_wrong_day_counts_are_rejected() -> None:
    metrics = compute([seg("FRA", "HND", "2026-07-01T13:00", "2026-07-02T08:30")])
    draft = _draft_for(metrics)
    draft["phases"][-1]["days"] = draft["phases"][-1]["days"][:2]
    with pytest.raises(ContractViolationError):
        assemble_plan(metrics, PlanDraft.model_validate(draft))


def _guide(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "destination": "Thailand",
        "summary": "Hot and humid; mosquito-borne illness is the main risk.",
        "sections": [{"key": "prevention", "title": "Prevention", "bullets": ["Use repellent."]}],
        "seasonal": [
            {
                "season": "Rainy season",
                "weather": "Heavy rain",
                "health_risk": "Dengue",
                "action": "Use repellent at dawn and dusk.",
            }
        ],
        "scope_check": {"passed": True},
    }
    data.update(overrides)
    return data


def test_guides_are_capped_and_scoped() -> None:
    HealthGuideContent.model_validate(_guide())
    too_long = _guide(sections=[{"key": "prevention", "title": "P", "bullets": ["x"] * 7}])
    tourism = _guide(sections=[{"key": "restaurants", "title": "Eat", "bullets": ["Pad thai"]}])
    for data in (too_long, tourism):
        with pytest.raises(ValidationError):
            HealthGuideContent.model_validate(data)


def _gateway(stage: str, answer: Any) -> AIGateway:
    chat = ScriptedChatModel({stage: answer})
    return AIGateway(reasoning=chat, fast=chat, embeddings=HashEmbeddingModel(dimensions=8))


async def test_guides_failing_their_scope_check_are_not_saved() -> None:
    gateway = _gateway("health_guide", lambda _: _guide(scope_check={"passed": False}))
    with pytest.raises(GuideOutOfScopeError):
        await generate_health_guide(gateway, PromptRegistry(environ={}), country="TH", region="")


async def test_plan_generation_uses_the_computed_phases() -> None:
    metrics = compute([seg("FRA", "JFK", "2026-07-01T10:00", "2026-07-01T12:45")])

    def coach(request: ChatRequest) -> dict[str, Any]:
        facts = json.loads(request.messages[-1].content)
        assert facts["direction"] == "westbound"
        return _draft_for(metrics)

    plan = await generate_jetlag_plan(
        _gateway("jetlag_plan", coach), PromptRegistry(environ={}), metrics
    )
    assert plan.schema_version == "jetlag-plan/1"
    assert plan.days[-1].phase == "arrival"


def test_guide_bullets_are_translatable_leaves() -> None:
    leaves = collect_leaves(_guide(), keys=GUIDE_TRANSLATABLE_KEYS)
    assert leaves["sections.0.bullets.0"] == "Use repellent."
    assert "destination" not in leaves


def test_trip_dates_are_validated() -> None:
    with pytest.raises(ValidationError):
        TripCreate(
            destination_country="th", start_date=date(2026, 7, 10), end_date=date(2026, 7, 1)
        )
