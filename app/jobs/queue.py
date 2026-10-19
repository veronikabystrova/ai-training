"""``JobQueue`` adapter over ARQ (enqueue side; the worker lives in ``app.jobs.worker``)."""

import asyncio
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings


class ArqJobQueue:
    """The Redis pool opens on first use, so building the app needs no Redis connection."""

    def __init__(self, redis_url: str) -> None:
        self._settings = RedisSettings.from_dsn(redis_url)
        self._pool: ArqRedis | None = None
        self._lock = asyncio.Lock()

    async def _get_pool(self) -> ArqRedis:
        async with self._lock:
            if self._pool is None:
                self._pool = await create_pool(self._settings)
            return self._pool

    async def enqueue(self, name: str, **kwargs: str) -> None:
        pool = await self._get_pool()
        job_kwargs: dict[str, Any] = dict(kwargs)  # enqueue_job types its own options, not ours
        await pool.enqueue_job(name, **job_kwargs)

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
