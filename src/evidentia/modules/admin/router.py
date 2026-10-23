from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, ConfigDict, Field

from evidentia.api.deps import SessionDep, SettingsDep
from evidentia.core.crypto import SecretBox
from evidentia.modules.admin.models import PromptOverride
from evidentia.modules.admin.store import PromptService, SettingsStore
from evidentia.modules.auth.dependencies import AdminUser

router = APIRouter(prefix="/admin", tags=["admin: configuration"])

SettingKey = Annotated[str, Path(pattern=r"^[a-z_]+\.[a-z_]+$")]
PromptKey = Annotated[str, Path(pattern=r"^[a-z_]+\.[a-z_]+$")]


def get_settings_store(session: SessionDep, settings: SettingsDep) -> SettingsStore:
    return SettingsStore(session, settings, SecretBox(settings.security.encryption_keys))


def get_prompt_service(session: SessionDep) -> PromptService:
    return PromptService(session)


StoreDep = Annotated[SettingsStore, Depends(get_settings_store)]
PromptsDep = Annotated[PromptService, Depends(get_prompt_service)]


class SettingRead(BaseModel):
    key: str
    description: str
    secret: bool
    source: str
    value: str | None = Field(description="Secrets are masked.")


class SettingWrite(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


class PromptRead(BaseModel):
    key: str
    default_version: str
    active_version: str
    source: str
    text: str
    override_id: uuid.UUID | None


class PromptWrite(BaseModel):
    version: str = Field(pattern=r"^\d+(\.\d+){0,2}$", description="Semantic-ish, e.g. 1.15")
    text: str = Field(min_length=20, max_length=20_000)


class PromptOverrideRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    version: str
    text: str
    is_active: bool
    created_by_id: uuid.UUID | None
    created_at: datetime


@router.get("/settings", response_model=list[SettingRead])
async def list_settings(_: AdminUser, store: StoreDep) -> list[dict[str, Any]]:
    return await store.describe()


@router.put("/settings/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def put_setting(
    key: SettingKey, payload: SettingWrite, user: AdminUser, store: StoreDep
) -> None:
    """Secrets are encrypted at rest; reads only ever return a masked value."""
    await store.put(key, payload.value, actor_id=user.id)


@router.delete("/settings/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_setting(key: SettingKey, user: AdminUser, store: StoreDep) -> None:
    """Removes the stored value; the environment value (if any) applies again."""
    await store.delete(key, actor_id=user.id)


@router.get("/prompts", response_model=list[PromptRead])
async def list_prompts(_: AdminUser, prompts: PromptsDep) -> list[dict[str, Any]]:
    return await prompts.describe()


@router.get("/prompts/{key}/history", response_model=list[PromptOverrideRead])
async def prompt_history(key: PromptKey, _: AdminUser, prompts: PromptsDep) -> list[PromptOverride]:
    return await prompts.history(key)


@router.put("/prompts/{key}", response_model=PromptOverrideRead)
async def activate_prompt(
    key: PromptKey, payload: PromptWrite, user: AdminUser, prompts: PromptsDep
) -> PromptOverride:
    """Tunes instruction wording without a deploy. Output contracts stay in code."""
    return await prompts.activate(key, version=payload.version, text=payload.text, actor_id=user.id)


@router.delete("/prompts/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def revert_prompt(key: PromptKey, user: AdminUser, prompts: PromptsDep) -> None:
    await prompts.revert(key, actor_id=user.id)
