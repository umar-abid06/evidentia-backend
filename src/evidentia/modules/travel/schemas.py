from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evidentia.core.i18n import Language
from evidentia.modules.auth.schemas import CountryCode
from evidentia.modules.travel.jetlag import FlightSegment
from evidentia.modules.travel.models import GenerationStatus


class TripCreate(BaseModel):
    destination_country: CountryCode
    destination_region: str = Field(default="", max_length=10, description="e.g. ISO 3166-2 suffix")
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _dates(self) -> TripCreate:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class TripRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    destination_country: str
    destination_region: str
    start_date: date
    end_date: date
    health_guide_id: uuid.UUID | None
    created_at: datetime


class GuideView(BaseModel):
    guide_id: uuid.UUID
    language: Language
    version: int
    status: GenerationStatus
    content: dict[str, Any] | None
    expires_at: datetime | None
    update_available: bool = Field(description="A newer guide exists for this destination.")


class JetLagPlanCreate(BaseModel):
    trip_id: uuid.UUID | None = None
    segments: list[FlightSegment] = Field(min_length=1, max_length=6)


class JetLagPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trip_id: uuid.UUID | None
    status: GenerationStatus
    schema_version: str
    metrics: dict[str, Any]
    plan: dict[str, Any] | None
    created_at: datetime
