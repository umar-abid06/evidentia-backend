from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from evidentia.modules.auth.schemas import CountryCode
from evidentia.modules.cards.models import (
    BinReviewReason,
    BinReviewStatus,
    MatchSource,
)

Bin = Annotated[
    str,
    StringConstraints(pattern=r"^\d{6,8}$", strip_whitespace=True),
    Field(description="First 6-8 digits of the card. Never send the full card number."),
]


class IdentifyRequest(BaseModel):
    bin: Bin
    country_code: CountryCode | None = None


class CandidateCard(BaseModel):
    card_product_id: uuid.UUID
    display_name: str
    issuer_name: str
    confidence: float


class IdentifyResult(BaseModel):
    outcome: Literal["matched", "ambiguous", "not_found", "unavailable"]
    match: CandidateCard | None = None
    candidates: list[CandidateCard] = Field(default_factory=list)
    source: MatchSource | None = None


class AddCardRequest(BaseModel):
    bin: Bin
    card_product_id: uuid.UUID | None = Field(
        default=None, description="Manual choice after an ambiguous identification."
    )
    nickname: str | None = Field(default=None, max_length=80)
    country_code: CountryCode | None = None


class CardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bin: str
    nickname: str | None
    card_product_id: uuid.UUID
    display_name: str
    issuer_name: str
    policy_id: uuid.UUID | None
    match_source: MatchSource
    match_confidence: float
    created_at: datetime


class BinReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bin: str
    country_code: str
    reason: BinReviewReason
    status: BinReviewStatus
    candidates: list[Any]
    lookup_response: list[Any]
    resolved_card_product_id: uuid.UUID | None
    created_at: datetime


class ResolveBinRequest(BaseModel):
    card_product_id: uuid.UUID
