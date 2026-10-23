"""Two-layer prompts.

The *instruction* layer is configuration: it resolves from the database, then the
environment, then the built-in default, so wording can be tuned without a deploy.
The *output contract* layer lives in code next to the schema it describes, so
nobody can break a parser by editing configuration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from evidentia.ai.types import ChatMessage

PromptSource = Literal["database", "environment", "default"]
ENV_PREFIX = "EVIDENTIA_PROMPT_"


@dataclass(frozen=True, slots=True)
class PromptSpec:
    key: str
    version: str
    text: str

    @property
    def env_var(self) -> str:
        return ENV_PREFIX + self.key.upper().replace(".", "_").replace("-", "_")


@dataclass(frozen=True, slots=True)
class ResolvedPrompt:
    key: str
    version: str
    text: str
    source: PromptSource


class PromptOverrides(Protocol):
    """A store of operator-edited instruction text, keyed by prompt key."""

    async def active(self, key: str) -> tuple[str, str] | None:
        """Returns ``(version, text)`` of the active override, if any."""
        ...


class PromptRegistry:
    def __init__(
        self,
        overrides: PromptOverrides | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._overrides = overrides
        self._environ = os.environ if environ is None else environ

    async def resolve(self, spec: PromptSpec) -> ResolvedPrompt:
        if self._overrides is not None:
            found = await self._overrides.active(spec.key)
            if found is not None:
                version, text = found
                return ResolvedPrompt(spec.key, version, text, "database")
        env_text = self._environ.get(spec.env_var)
        if env_text:
            return ResolvedPrompt(spec.key, f"{spec.version}+env", env_text, "environment")
        return ResolvedPrompt(spec.key, spec.version, spec.text, "default")


def compose_messages(
    prompt: ResolvedPrompt, *, contract: str, user_content: str
) -> list[ChatMessage]:
    system = f"{prompt.text.strip()}\n\n## OUTPUT CONTRACT\n{contract.strip()}"
    return [ChatMessage("system", system), ChatMessage("user", user_content)]
