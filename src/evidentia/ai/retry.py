"""Bounded exponential backoff for transient provider failures only."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

import structlog

from evidentia.ai.errors import AIError

DEFAULT_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)

log = structlog.get_logger(__name__)


async def retry_transient[T](
    call: Callable[[], Awaitable[T]],
    *,
    delays: Sequence[float] = DEFAULT_DELAYS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[T, int]:
    """Runs ``call``; on a transient error waits ``delays[n]`` and tries again.

    Returns the result and the number of retries used. Permanent errors (bad
    input, auth) propagate immediately; the raised error carries ``attempts``.
    """
    retries = 0
    while True:
        try:
            return await call(), retries
        except AIError as exc:
            exc.attempts = retries + 1
            if not exc.transient or retries >= len(delays):
                raise
            delay = delays[retries]
            log.warning("ai_retry", error=type(exc).__name__, retry=retries + 1, delay_s=delay)
            await sleep(delay)
            retries += 1
