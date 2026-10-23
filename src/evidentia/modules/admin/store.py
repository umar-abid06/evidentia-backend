"""System settings and prompt overrides, resolved database-first with env fallback."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.ai.factory import build_gateway
from evidentia.ai.gateway import AIGateway
from evidentia.ai.prompts import PromptRegistry, PromptSpec
from evidentia.ai.telemetry import LLMTelemetry
from evidentia.core.config import Settings
from evidentia.core.crypto import SecretBox
from evidentia.core.errors import NotFoundError
from evidentia.core.metrics import prometheus_sink
from evidentia.modules.admin.models import PromptOverride, SystemSetting
from evidentia.modules.assistant.prompting import ASSISTANT
from evidentia.modules.audit.models import record_audit
from evidentia.modules.extraction import prompts as extraction_prompts
from evidentia.modules.knowledge.translation import TRANSLATOR
from evidentia.modules.travel.generation import HEALTH_GUIDE, JETLAG

Source = Literal["database", "environment", "unset"]


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value else None


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    secret: bool
    description: str
    from_env: Callable[[Settings], str | None]


KNOWN_SETTINGS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        SettingSpec(
            "llm.openai_api_key",
            True,
            "Model provider API key.",
            lambda s: _secret(s.llm.openai_api_key),
        ),
        SettingSpec(
            "cards.bin_lookup_api_key",
            True,
            "Card-range lookup API key.",
            lambda s: _secret(s.cards.bin_lookup_api_key),
        ),
        SettingSpec(
            "crm.webhook_url", False, "CRM contact upsert URL.", lambda s: s.crm.webhook_url
        ),
        SettingSpec("crm.api_key", True, "CRM API key.", lambda s: _secret(s.crm.api_key)),
    )
}

PROMPT_CATALOG: dict[str, PromptSpec] = {
    spec.key: spec
    for spec in (
        extraction_prompts.DISCOVERY,
        extraction_prompts.CLASSIFIER,
        extraction_prompts.ORGANIZER,
        TRANSLATOR,
        ASSISTANT,
        HEALTH_GUIDE,
        JETLAG,
    )
}


def mask(value: str) -> str:
    return "****" + value[-4:] if len(value) > 8 else "****"


class SettingsStore:
    def __init__(self, session: AsyncSession, settings: Settings, box: SecretBox) -> None:
        self.session = session
        self.settings = settings
        self.box = box

    async def get(self, key: str) -> str | None:
        spec = self._spec(key)
        row = await self.session.get(SystemSetting, key)
        if row is not None:
            return self.box.decrypt(row.value) if row.is_secret else row.value
        return spec.from_env(self.settings)

    async def describe(self) -> list[dict[str, Any]]:
        rows = {row.key: row for row in await self.session.scalars(select(SystemSetting))}
        described = []
        for key, spec in KNOWN_SETTINGS.items():
            source: Source = "unset"
            value: str | None = None
            if key in rows:
                source = "database"
                row = rows[key]
                value = self.box.decrypt(row.value) if row.is_secret else row.value
            elif (env_value := spec.from_env(self.settings)) is not None:
                source, value = "environment", env_value
            described.append(
                {
                    "key": key,
                    "description": spec.description,
                    "secret": spec.secret,
                    "source": source,
                    "value": mask(value) if spec.secret and value else value,
                }
            )
        return described

    async def put(self, key: str, value: str, *, actor_id: uuid.UUID) -> None:
        spec = self._spec(key)
        stored = self.box.encrypt(value) if spec.secret else value
        row = await self.session.get(SystemSetting, key)
        if row is None:
            self.session.add(
                SystemSetting(key=key, value=stored, is_secret=spec.secret, updated_by_id=actor_id)
            )
        else:
            row.value, row.is_secret, row.updated_by_id = stored, spec.secret, actor_id
        record_audit(
            self.session,
            actor_id=actor_id,
            action="setting.updated",
            entity_type="setting",
            entity_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
            payload={"key": key},
        )
        await self.session.commit()

    async def delete(self, key: str, *, actor_id: uuid.UUID) -> None:
        self._spec(key)
        row = await self.session.get(SystemSetting, key)
        if row is not None:
            await self.session.delete(row)
            record_audit(
                self.session,
                actor_id=actor_id,
                action="setting.cleared",
                entity_type="setting",
                entity_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
                payload={"key": key},
            )
            await self.session.commit()

    @staticmethod
    def _spec(key: str) -> SettingSpec:
        spec = KNOWN_SETTINGS.get(key)
        if spec is None:
            raise NotFoundError(f"Unknown setting '{key}'.", code="setting_unknown")
        return spec


class DatabasePromptOverrides:
    """The database layer of prompt resolution (database -> environment -> default)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active(self, key: str) -> tuple[str, str] | None:
        row = (
            await self.session.execute(
                select(PromptOverride.version, PromptOverride.text).where(
                    PromptOverride.key == key, PromptOverride.is_active.is_(True)
                )
            )
        ).first()
        return (row.version, row.text) if row else None


def prompt_registry(session: AsyncSession) -> PromptRegistry:
    return PromptRegistry(DatabasePromptOverrides(session))


class PromptService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def describe(self) -> list[dict[str, Any]]:
        active = {
            row.key: row
            for row in await self.session.scalars(
                select(PromptOverride).where(PromptOverride.is_active.is_(True))
            )
        }
        registry = PromptRegistry(DatabasePromptOverrides(self.session))
        described = []
        for key, spec in PROMPT_CATALOG.items():
            resolved = await registry.resolve(spec)
            described.append(
                {
                    "key": key,
                    "default_version": spec.version,
                    "active_version": resolved.version,
                    "source": resolved.source,
                    "text": resolved.text,
                    "override_id": active[key].id if key in active else None,
                }
            )
        return described

    async def history(self, key: str) -> list[PromptOverride]:
        self._spec(key)
        result = await self.session.scalars(
            select(PromptOverride)
            .where(PromptOverride.key == key)
            .order_by(PromptOverride.created_at.desc())
        )
        return list(result)

    async def activate(
        self, key: str, *, version: str, text: str, actor_id: uuid.UUID
    ) -> PromptOverride:
        self._spec(key)
        await self._deactivate(key)
        override = PromptOverride(key=key, version=version, text=text, created_by_id=actor_id)
        self.session.add(override)
        record_audit(
            self.session,
            actor_id=actor_id,
            action="prompt.activated",
            entity_type="prompt",
            entity_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
            payload={"key": key, "version": version},
        )
        await self.session.commit()
        return override

    async def revert(self, key: str, *, actor_id: uuid.UUID) -> None:
        self._spec(key)
        await self._deactivate(key)
        record_audit(
            self.session,
            actor_id=actor_id,
            action="prompt.reverted",
            entity_type="prompt",
            entity_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
            payload={"key": key},
        )
        await self.session.commit()

    async def _deactivate(self, key: str) -> None:
        await self.session.execute(
            update(PromptOverride)
            .where(PromptOverride.key == key, PromptOverride.is_active.is_(True))
            .values(is_active=False)
        )
        await self.session.flush()

    @staticmethod
    def _spec(key: str) -> PromptSpec:
        spec = PROMPT_CATALOG.get(key)
        if spec is None:
            raise NotFoundError(f"Unknown prompt '{key}'.", code="prompt_unknown")
        return spec


async def resolve_gateway(session: AsyncSession, settings: Settings) -> AIGateway:
    """Builds a gateway with the provider key from encrypted settings (env fallback)."""
    box = SecretBox(settings.security.encryption_keys)
    key = await SettingsStore(session, settings, box).get("llm.openai_api_key")
    return build_gateway(settings.llm, api_key=key)


class CachedGatewaySource:
    """API-side gateway that picks up a rotated provider key within ``ttl_s``."""

    def __init__(self, settings: Settings, *, ttl_s: float = 60.0) -> None:
        self.settings = settings
        self.box = SecretBox(settings.security.encryption_keys)
        self.ttl_s = ttl_s
        self._gateway: AIGateway | None = None
        self._key: str | None = None
        self._fresh_until = 0.0

    async def get(self, session: AsyncSession) -> AIGateway:
        now = time.monotonic()
        if self._gateway is not None and now < self._fresh_until:
            return self._gateway
        key = await SettingsStore(session, self.settings, self.box).get("llm.openai_api_key")
        if self._gateway is None or key != self._key:
            # Shared across requests: mirror to Prometheus but keep no records in memory.
            telemetry = LLMTelemetry([prometheus_sink], keep_records=False)
            self._gateway = build_gateway(self.settings.llm, api_key=key, telemetry=telemetry)
            self._key = key
        self._fresh_until = now + self.ttl_s
        return self._gateway
