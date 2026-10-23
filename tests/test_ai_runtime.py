from __future__ import annotations

import asyncio
import math

import pytest

from evidentia.ai.concurrency import gather_limited
from evidentia.ai.errors import (
    ContractViolationError,
    MalformedJSONError,
    NotAJSONObjectError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderRequestError,
)
from evidentia.ai.factory import build_gateway
from evidentia.ai.gateway import AIGateway
from evidentia.ai.prompts import PromptRegistry, PromptSpec, compose_messages
from evidentia.ai.providers.fake import HashEmbeddingModel, ScriptedChatModel
from evidentia.ai.retry import retry_transient
from evidentia.ai.structured import parse_json_object, sanitize, validate_contract
from evidentia.ai.types import ChatMessage, ChatRequest, ChatResult
from evidentia.core.config import LLMSettings


class _NoSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def test_transient_errors_back_off_one_two_four_seconds() -> None:
    sleep = _NoSleep()
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise ProviderRateLimitedError("slow down")
        return "ok"

    result, retries = await retry_transient(flaky, sleep=sleep)
    assert (result, retries) == ("ok", 3)
    assert sleep.delays == [1.0, 2.0, 4.0]


async def test_retries_give_up_after_three_extra_attempts() -> None:
    async def always_limited() -> str:
        raise ProviderRateLimitedError("slow down")

    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await retry_transient(always_limited, sleep=_NoSleep())
    assert excinfo.value.attempts == 4


async def test_bad_input_fails_fast() -> None:
    sleep = _NoSleep()

    async def bad_request() -> str:
        raise ProviderRequestError("bad input")

    with pytest.raises(ProviderRequestError):
        await retry_transient(bad_request, sleep=sleep)
    assert sleep.delays == []


def test_strict_parser_reports_a_preview() -> None:
    with pytest.raises(MalformedJSONError) as excinfo:
        parse_json_object('{"items": [1, 2,', stage="discovery")
    assert excinfo.value.preview.startswith('{"items"')
    assert excinfo.value.stage == "discovery"


def test_strict_parser_rejects_non_objects() -> None:
    with pytest.raises(NotAJSONObjectError):
        parse_json_object("[1, 2, 3]")


def test_sanitize_strips_characters_postgres_cannot_store() -> None:
    assert sanitize({"a\x00": ["x\x00y", "tab\tok"]}) == {"a": ["xy", "tab\tok"]}


def test_contract_violation_lists_errors() -> None:
    from pydantic import BaseModel

    class Contract(BaseModel):
        count: int

    with pytest.raises(ContractViolationError) as excinfo:
        validate_contract(Contract, {"count": "many"}, stage="classify")
    assert excinfo.value.errors[0]["loc"] == ["count"]


async def test_gateway_records_tokens_latency_and_retries() -> None:
    chat = ScriptedChatModel({"discovery": lambda _: {"items": []}})
    calls = 0
    original = chat.complete

    async def flaky(request: ChatRequest) -> ChatResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderRateLimitedError("busy")
        return await original(request)

    chat.complete = flaky  # type: ignore[method-assign]
    gateway = AIGateway(
        reasoning=chat, fast=chat, embeddings=HashEmbeddingModel(), sleep=_NoSleep()
    )
    data = await gateway.complete_json(
        stage="discovery", messages=[ChatMessage("user", "read this")]
    )
    assert data == {"items": []}
    [record] = gateway.telemetry.records
    assert record.stage == "discovery"
    assert record.retries == 1
    assert record.success
    assert record.payload_bytes == len(b"read this")


async def test_gateway_records_failures_too() -> None:
    chat = ScriptedChatModel({"organize": lambda _: "not json"})
    gateway = AIGateway(reasoning=chat, fast=chat, embeddings=HashEmbeddingModel())
    with pytest.raises(MalformedJSONError):
        await gateway.complete_json(stage="organize", messages=[ChatMessage("user", "x")])
    report = gateway.telemetry.report(pages=2)
    assert report.calls == 1
    assert report.stages[0].stage == "organize"


async def test_unconfigured_provider_refuses_calls() -> None:
    gateway = build_gateway(LLMSettings(provider="openai", openai_api_key=None))
    assert not gateway.is_configured
    with pytest.raises(ProviderNotConfiguredError):
        await gateway.complete_text(stage="answer", messages=[ChatMessage("user", "hi")])


async def test_hash_embeddings_rank_related_text_higher() -> None:
    model = HashEmbeddingModel(dimensions=256)
    result = await model.embed(
        ["emergency medical expenses abroad", "medical expenses abroad", "baggage delay"]
    )
    query, related, unrelated = result.vectors

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert math.isclose(sum(v * v for v in query), 1.0, rel_tol=1e-9)
    assert cosine(query, related) > cosine(query, unrelated)


async def test_prompt_resolution_order_is_database_env_default() -> None:
    spec = PromptSpec(key="extraction.discovery", version="2", text="default text")

    class Overrides:
        def __init__(self, value: tuple[str, str] | None) -> None:
            self.value = value

        async def active(self, key: str) -> tuple[str, str] | None:
            return self.value

    env = {spec.env_var: "env text"}
    from_db = await PromptRegistry(Overrides(("7", "db text")), environ=env).resolve(spec)
    from_env = await PromptRegistry(Overrides(None), environ=env).resolve(spec)
    default = await PromptRegistry(environ={}).resolve(spec)

    assert (from_db.source, from_db.text, from_db.version) == ("database", "db text", "7")
    assert (from_env.source, from_env.text) == ("environment", "env text")
    assert (default.source, default.version) == ("default", "2")
    assert spec.env_var == "EVIDENTIA_PROMPT_EXTRACTION_DISCOVERY"


async def test_contract_is_appended_from_code() -> None:
    prompt = await PromptRegistry(environ={}).resolve(PromptSpec("k", "1", "Be precise."))
    system, user = compose_messages(prompt, contract='{"a": 1}', user_content="payload")
    assert system.content.endswith('## OUTPUT CONTRACT\n{"a": 1}')
    assert user.content == "payload"


async def test_gather_limited_caps_concurrency_and_keeps_order() -> None:
    in_flight = peak = 0

    def job(value: int):  # type: ignore[no-untyped-def]
        async def run() -> int:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1
            return value

        return run

    results = await gather_limited([job(i) for i in range(20)], limit=6)
    assert results == list(range(20))
    assert peak <= 6


async def test_gather_limited_surfaces_the_original_error() -> None:
    async def ok() -> int:
        return 1

    async def broken() -> int:
        raise ProviderRequestError("bad chunk")

    with pytest.raises(ProviderRequestError, match="bad chunk"):
        await gather_limited([ok, broken, ok], limit=2)
