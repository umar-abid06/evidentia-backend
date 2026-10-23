"""Jet-lag maths in pure code: time-zone shift, direction, stopovers, recovery days.

No model is involved here, so the numbers are always right. The model only writes
the coaching text for the phases this module defines.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from functools import cache
from typing import Literal
from zoneinfo import ZoneInfo

import airportsdata
from pydantic import BaseModel, Field, field_validator

Direction = Literal["eastbound", "westbound", "none"]

STOPOVER_RECOVERY_HOURS = 24  # a layover this long gets its own recovery phase


class UnknownAirportError(ValueError):
    pass


class FlightSegment(BaseModel):
    origin: str = Field(min_length=3, max_length=3, description="IATA code")
    destination: str = Field(min_length=3, max_length=3, description="IATA code")
    departure: datetime = Field(description="Local departure time at the origin (no offset).")
    arrival: datetime = Field(description="Local arrival time at the destination (no offset).")

    @field_validator("origin", "destination")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class SegmentMetrics(BaseModel):
    origin: str
    destination: str
    departure_utc: datetime
    arrival_utc: datetime
    duration_hours: float
    shift_hours: float


class StopoverMetrics(BaseModel):
    airport: str
    hours: float
    shift_hours_from_origin: float
    recovery_days: int


class Phase(BaseModel):
    key: str
    label: str
    days: int
    location: str | None = None


class JetLagMetrics(BaseModel):
    total_shift_hours: float
    direction: Direction
    recovery_days: int
    segments: list[SegmentMetrics]
    stopovers: list[StopoverMetrics]
    phases: list[Phase]


@cache
def _airports() -> dict[str, airportsdata.Airport]:
    return airportsdata.load("IATA")


def airport_zone(iata: str) -> ZoneInfo:
    airport = _airports().get(iata.upper())
    if airport is None or not airport["tz"]:
        raise UnknownAirportError(f"Unknown airport code: {iata}")
    return ZoneInfo(airport["tz"])


def _offset_hours(zone: ZoneInfo, local: datetime) -> float:
    offset = local.replace(tzinfo=zone).utcoffset()
    return offset.total_seconds() / 3600 if offset else 0.0


def normalize_shift(hours: float) -> float:
    """The body clock shifts the short way round: result is in (-12, 12]."""
    shifted = (hours + 12) % 24 - 12
    return 12.0 if shifted == -12 else round(shifted, 2)


def direction_of(shift: float) -> Direction:
    if abs(shift) < 1:
        return "none"
    return "eastbound" if shift > 0 else "westbound"


def recovery_days(shift: float) -> int:
    """About one day per hour eastbound, one per 1.5 hours westbound."""
    magnitude = abs(shift)
    if magnitude < 1:
        return 0
    if magnitude < 3:
        return 1
    return math.ceil(magnitude / (1.0 if shift > 0 else 1.5))


def compute(segments: list[FlightSegment]) -> JetLagMetrics:
    if not segments:
        raise ValueError("At least one flight segment is required.")
    first_zone = airport_zone(segments[0].origin)
    home_offset = _offset_hours(first_zone, segments[0].departure)

    seg_metrics: list[SegmentMetrics] = []
    for segment in segments:
        origin_zone, dest_zone = airport_zone(segment.origin), airport_zone(segment.destination)
        departure_utc = segment.departure.replace(tzinfo=origin_zone).astimezone(UTC)
        arrival_utc = segment.arrival.replace(tzinfo=dest_zone).astimezone(UTC)
        if arrival_utc <= departure_utc:
            raise ValueError(f"{segment.origin}->{segment.destination} arrives before it departs.")
        seg_metrics.append(
            SegmentMetrics(
                origin=segment.origin,
                destination=segment.destination,
                departure_utc=departure_utc,
                arrival_utc=arrival_utc,
                duration_hours=round((arrival_utc - departure_utc).total_seconds() / 3600, 2),
                shift_hours=normalize_shift(
                    _offset_hours(dest_zone, segment.arrival)
                    - _offset_hours(origin_zone, segment.departure)
                ),
            )
        )

    stopovers: list[StopoverMetrics] = []
    for previous, following, raw in zip(seg_metrics, seg_metrics[1:], segments[1:], strict=False):
        layover = (following.departure_utc - previous.arrival_utc).total_seconds() / 3600
        if layover < 0:
            raise ValueError(f"Connection at {raw.origin} departs before the previous arrival.")
        shift = normalize_shift(
            _offset_hours(airport_zone(raw.origin), raw.departure) - home_offset
        )
        if layover >= STOPOVER_RECOVERY_HOURS:
            stopovers.append(
                StopoverMetrics(
                    airport=raw.origin,
                    hours=round(layover, 2),
                    shift_hours_from_origin=shift,
                    recovery_days=max(1, min(recovery_days(shift), math.floor(layover / 24))),
                )
            )

    last = segments[-1]
    total = normalize_shift(
        _offset_hours(airport_zone(last.destination), last.arrival) - home_offset
    )
    days = recovery_days(total)
    phases = [
        Phase(key="pre_departure", label="Before you fly", days=2 if abs(total) >= 3 else 1),
        Phase(key="travel", label="Travel day", days=1),
        *(
            Phase(
                key=f"stopover_{stop.airport.lower()}",
                label=f"Stopover in {stop.airport}",
                days=stop.recovery_days,
                location=stop.airport,
            )
            for stop in stopovers
        ),
        Phase(key="arrival", label="After you land", days=max(days, 1), location=last.destination),
    ]
    return JetLagMetrics(
        total_shift_hours=total,
        direction=direction_of(total),
        recovery_days=days,
        segments=seg_metrics,
        stopovers=stopovers,
        phases=phases,
    )
