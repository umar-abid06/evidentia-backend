"""Builds an AIGateway for the configured provider."""

from __future__ import annotations

from openai import AsyncOpenAI

from evidentia.ai.gateway import AIGateway
from evidentia.ai.providers.fake import HashEmbeddingModel, ScriptedChatModel
from evidentia.ai.providers.openai import OpenAIChatModel, OpenAIEmbeddingModel
from evidentia.ai.telemetry import LLMTelemetry
from evidentia.core.config import LLMSettings


def build_gateway(
    config: LLMSettings,
    *,
    telemetry: LLMTelemetry | None = None,
    api_key: str | None = None,
) -> AIGateway:
    """``api_key`` overrides the environment value (e.g. a key stored encrypted in the DB)."""
    if config.provider == "fake":
        return AIGateway(
            reasoning=ScriptedChatModel(model=f"fake/{config.reasoning_model}"),
            fast=ScriptedChatModel(model=f"fake/{config.chat_model}"),
            embeddings=HashEmbeddingModel(dimensions=config.embedding_dimensions),
            telemetry=telemetry,
            retry_delays=config.retry_delays_s,
        )

    key = api_key or (config.openai_api_key.get_secret_value() if config.openai_api_key else None)
    client = AsyncOpenAI(
        api_key=key or "unconfigured",
        base_url=config.openai_base_url,
        max_retries=0,  # retries are owned by the gateway
    )
    return AIGateway(
        reasoning=OpenAIChatModel(
            client, config.reasoning_model, timeout_s=config.reasoning_timeout_s
        ),
        fast=OpenAIChatModel(client, config.chat_model, timeout_s=config.chat_timeout_s),
        embeddings=OpenAIEmbeddingModel(
            client,
            config.embedding_model,
            dimensions=config.embedding_dimensions,
            timeout_s=config.chat_timeout_s,
        ),
        telemetry=telemetry,
        retry_delays=config.retry_delays_s,
        configured=bool(key),
    )
