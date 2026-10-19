"""At-most-once guard for background-job side effects (emails)."""

from typing import Protocol

from redis.asyncio import Redis


class OnceStore(Protocol):
    async def claim(self, key: str) -> bool:
        """True for the first caller of ``key``; False once it is claimed."""
        ...

    async def release(self, key: str) -> None:
        """Give a claim back so a retry can send (the first attempt failed)."""
        ...


class RedisOnceStore:
    """``SET key NX EX``: atomic across worker processes."""

    def __init__(self, redis: Redis, *, ttl_seconds: int = 7 * 24 * 3600) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def claim(self, key: str) -> bool:
        return bool(await self._redis.set(f"job-once:{key}", "1", nx=True, ex=self._ttl))

    async def release(self, key: str) -> None:
        await self._redis.delete(f"job-once:{key}")
