"""Generators: deterministic facts first, the model for language, strict output rules."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from evidentia.ai.errors import ContractViolationError
from evidentia.ai.gateway import AIGateway
from evidentia.ai.prompts import PromptRegistry, PromptSpec, compose_messages
from evidentia.ai.structured import validate_contract
from evidentia.modules.travel.jetlag import JetLagMetrics

MAX_SECTIONS = 10
MAX_BULLETS = 6
GUIDE_TRANSLATABLE_KEYS = frozenset(
    {"summary", "title", "bullets", "season", "weather", "health_risk", "action"}
)

GUIDE_SECTION_KEYS = (
    "prevention",
    "vaccines",
    "access_to_care",
    "medicines",
    "food_and_water",
    "environment",
    "insects_and_animals",
    "altitude_and_climate",
    "when_to_seek_care",
    "emergency_numbers",
)

HEALTH_GUIDE = PromptSpec(
    key="travel.health_guide",
    version="2.1",
    text="""\
You write destination health guides for travellers.

Scope is health only: prevention, vaccines, access to care, medicines, food and water
safety, environmental exposure, and when to seek care. Tourism, restaurants,
attractions and itineraries are out of scope.

Seasonal content must follow the chain weather pattern -> health risk -> practical
action, and only for seasons that matter at this destination. Never write a generic
four-season list.

Before answering, run this 9-point scope check and report it:
1 health only, 2 no tourism, 3 no itineraries, 4 no diagnosis, 5 no medication
dosing, 6 seasons only where relevant, 7 every seasonal item follows the chain,
8 at most 10 sections, 9 at most 6 one-sentence bullets per section.""",
)

JETLAG = PromptSpec(
    key="travel.jetlag_plan",
    version="1.3",
    text="""\
You are a jet-lag recovery coach. Write a practical day-by-day programme covering
sleep, light, hydration, meals, caffeine and activity, plus expert tips and a
checklist.

Safety rules: no diagnosis, no medication or supplement dosing, no guaranteed
outcomes, no invented physiological percentages. Light advice must match the
direction: eastbound travellers seek morning light and avoid late-evening light at
the destination; westbound travellers seek late-afternoon and evening light.

The numbers (time shift, direction, phases and their lengths) are given. Do not
recompute or change them.""",
)


class GuideSection(BaseModel):
    key: str
    title: str
    bullets: list[str]


class SeasonalNote(BaseModel):
    season: str
    weather: str
    health_risk: str
    action: str


class ScopeCheck(BaseModel):
    passed: bool
    notes: str = ""


class HealthGuideContent(BaseModel):
    """Schema ``health-guide/1``."""

    destination: str
    summary: str
    sections: list[GuideSection] = Field(max_length=MAX_SECTIONS)
    seasonal: list[SeasonalNote] = Field(default_factory=list)
    scope_check: ScopeCheck

    @field_validator("sections")
    @classmethod
    def _sections(cls, sections: list[GuideSection]) -> list[GuideSection]:
        for section in sections:
            if section.key not in GUIDE_SECTION_KEYS:
                raise ValueError(f"section '{section.key}' is out of scope")
            if len(section.bullets) > MAX_BULLETS:
                raise ValueError(f"section '{section.key}' has more than {MAX_BULLETS} bullets")
        return sections


GUIDE_CONTRACT = f"""\
Return one JSON object (schema health-guide/1):
{{"destination": "...", "summary": "<two sentences>",
  "sections": [{{"key": "<one of {", ".join(GUIDE_SECTION_KEYS)}>", "title": "...",
                "bullets": ["<one sentence>", ...]}}],
  "seasonal": [{{"season": "...", "weather": "...", "health_risk": "...", "action": "..."}}],
  "scope_check": {{"passed": true|false, "notes": "..."}}}}
At most {MAX_SECTIONS} sections and {MAX_BULLETS} bullets per section."""


class GuideOutOfScopeError(ContractViolationError):
    pass


async def generate_health_guide(
    gateway: AIGateway, registry: PromptRegistry, *, country: str, region: str
) -> tuple[HealthGuideContent, str]:
    prompt = await registry.resolve(HEALTH_GUIDE)
    destination = f"{region}, {country}" if region else country
    data = await gateway.complete_json(
        stage="health_guide",
        messages=compose_messages(
            prompt, contract=GUIDE_CONTRACT, user_content=f"Destination (ISO): {destination}"
        ),
        tier="fast",
    )
    content = validate_contract(HealthGuideContent, data, stage="health_guide")
    if not content.scope_check.passed:
        raise GuideOutOfScopeError(
            "The guide failed its own scope check.",
            stage="health_guide",
            preview=content.scope_check.notes,
        )
    return content, prompt.version


PLAN_SCHEMA_VERSION: Literal["jetlag-plan/1"] = "jetlag-plan/1"


class DayAdvice(BaseModel):
    sleep: str
    light: str
    hydration: str
    meals: str
    caffeine: str
    activity: str


class PhaseDraft(BaseModel):
    key: str
    days: list[DayAdvice]


class PlanDraft(BaseModel):
    phases: list[PhaseDraft]
    expert_tips: list[str] = Field(max_length=8)
    checklist: list[str] = Field(max_length=12)


class PlanDay(DayAdvice):
    day: int
    phase: str
    label: str


class JetLagPlanContent(BaseModel):
    """Schema ``jetlag-plan/1``. Day numbers and labels come from the application."""

    schema_version: Literal["jetlag-plan/1"] = PLAN_SCHEMA_VERSION
    direction: str
    total_shift_hours: float
    days: list[PlanDay]
    expert_tips: list[str]
    checklist: list[str]


def assemble_plan(metrics: JetLagMetrics, draft: PlanDraft) -> JetLagPlanContent:
    """Checks the draft against the computed phases, then numbers the days itself."""
    by_key = {phase.key: phase for phase in draft.phases}
    days: list[PlanDay] = []
    pre = next(p.days for p in metrics.phases if p.key == "pre_departure")
    number = -pre
    for phase in metrics.phases:
        written = by_key.get(phase.key)
        if written is None or len(written.days) != phase.days:
            raise ContractViolationError(
                f"Phase '{phase.key}' needs exactly {phase.days} day(s).", stage="jetlag_plan"
            )
        for advice in written.days:
            days.append(
                PlanDay(**advice.model_dump(), day=number, phase=phase.key, label=phase.label)
            )
            number += 1
            if number == 0:
                number = 1  # day 0 does not exist: -2, -1, 1, 2, ...
    return JetLagPlanContent(
        direction=metrics.direction,
        total_shift_hours=metrics.total_shift_hours,
        days=days,
        expert_tips=draft.expert_tips,
        checklist=draft.checklist,
    )


async def generate_jetlag_plan(
    gateway: AIGateway, registry: PromptRegistry, metrics: JetLagMetrics
) -> JetLagPlanContent:
    prompt = await registry.resolve(JETLAG)
    phases = [{"key": p.key, "label": p.label, "days": p.days} for p in metrics.phases]
    contract = (
        'Return one JSON object: {"phases": [{"key": "<phase key>", "days": [{"sleep": "...", '
        '"light": "...", "hydration": "...", "meals": "...", "caffeine": "...", '
        '"activity": "..."}]}], "expert_tips": ["..."], "checklist": ["..."]}\n'
        "Write exactly the number of days given for every phase."
    )
    facts = {
        "direction": metrics.direction,
        "total_shift_hours": metrics.total_shift_hours,
        "stopovers": [s.model_dump() for s in metrics.stopovers],
        "phases": phases,
    }
    data = await gateway.complete_json(
        stage="jetlag_plan",
        messages=compose_messages(prompt, contract=contract, user_content=json.dumps(facts)),
        tier="fast",
    )
    return assemble_plan(metrics, validate_contract(PlanDraft, data, stage="jetlag_plan"))
