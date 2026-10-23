from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from evidentia.modules.auth.schemas import CountryCode

PolicyCode = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,78}$", strip_whitespace=True)
]


class PolicyCreate(BaseModel):
    code: PolicyCode
    name: str = Field(max_length=200)
    display_name: str = Field(max_length=200)
    insurer_name: str = Field(max_length=200)
    is_active: bool = True


class PolicyUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    insurer_name: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class PolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    display_name: str
    insurer_name: str
    is_active: bool
    created_at: datetime


class CardProductCreate(BaseModel):
    issuer_name: str = Field(max_length=200)
    product_name: str = Field(max_length=200)
    display_name: str = Field(max_length=200)
    scheme: str | None = Field(default=None, max_length=40)
    funding_type: str = Field(default="credit", max_length=20)
    country_code: CountryCode
    policy_id: uuid.UUID | None = None
    is_active: bool = True


class CardProductUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    scheme: str | None = Field(default=None, max_length=40)
    funding_type: str | None = Field(default=None, max_length=20)
    policy_id: uuid.UUID | None = None
    is_active: bool | None = None


class CardProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    issuer_name: str
    product_name: str
    display_name: str
    scheme: str | None
    funding_type: str
    country_code: str
    policy_id: uuid.UUID | None
    is_active: bool
