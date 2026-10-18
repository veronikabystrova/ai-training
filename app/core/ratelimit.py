import time
from enum import Enum
from typing import cast

from limits import parse
from limits.aio.storage import RedisStorage
from limits.aio.storage.redis.redispy import RedispyBridge
from limits.aio.strategies import FixedWindowRateLimiter

from app.core.errors import RateLimitedError


class RouteClass(Enum):
    login_ip = "10/minute"
    login_email = "5/minute"
    register_ip = "20/hour"
    password_reset_email = "5/hour"  # noqa: S105  (rate string, not a credential)
    verify_resend_user = "3/hour"
    public_preview_ip = "60/minute"
    default_principal = "300/minute"


class RateLimiter:
    def __init__(self, redis_url: str) -> None:
        storage = RedisStorage(
            redis_url.replace("redis://", "async+redis://", 1), implementation="redispy"
        )
        self._storage = storage
        self._strategy = FixedWindowRateLimiter(storage)

    async def aclose(self) -> None:
        """Release the Redis connection pool. Call from app lifespan shutdown."""
        # limits has no public close(); the redis-py bridge owns the client (limits 5.8)
        await cast("RedispyBridge", self._storage.bridge).storage.aclose()

    async def hit(self, route: RouteClass, key: str) -> None:
        item = parse(route.value)
        ident = key.lower()
        if not await self._strategy.hit(item, route.name, ident):
            stats = await self._strategy.get_window_stats(item, route.name, ident)
            retry_after = max(1, int(stats.reset_time - time.time()))
            raise RateLimitedError(headers={"Retry-After": str(retry_after)})
