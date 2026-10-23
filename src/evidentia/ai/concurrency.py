"""Bounded fan-out for parallel model calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable


async def gather_limited[T](jobs: Iterable[Callable[[], Awaitable[T]]], *, limit: int) -> list[T]:
    """Runs jobs with at most ``limit`` in flight; results keep input order.

    Uses a TaskGroup, so the first failure cancels the remaining jobs. That failure
    is re-raised as itself (not an ExceptionGroup) so typed errors stay catchable.
    """
    semaphore = asyncio.Semaphore(limit)

    async def run(job: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await job()

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(run(job)) for job in jobs]
    except BaseExceptionGroup as group_error:
        first: BaseException = group_error
        while isinstance(first, BaseExceptionGroup):
            first = first.exceptions[0]
        raise first from group_error
    return [task.result() for task in tasks]
