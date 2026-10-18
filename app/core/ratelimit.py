import time
from enum import Enum
from typing import cast

import structlog
from limits import parse
from limits.aio.storage import RedisStorage
from limits.aio.storage.redis.redispy import RedispyBridge
from limits.aio.strategies import FixedWindowRateLimiter
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

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
        """Count a hit; raise ``RateLimitedError`` over the limit.

        Fail-open on a Redis outage (architecture ``rate_limit`` row): the request proceeds
        unthrottled and a warning is logged with the route class only (no key, no PII).
        """
        item = parse(route.value)
        ident = key.lower()
        try:
            allowed = await self._strategy.hit(item, route.name, ident)
            stats = (
                None if allowed else await self._strategy.get_window_stats(item, route.name, ident)
            )
        except (RedisConnectionError, RedisTimeoutError):
            structlog.get_logger(__name__).warning("rate_limit_unavailable", route_class=route.name)
            return
        if stats is not None:
            retry_after = max(1, int(stats.reset_time - time.time()))
            raise RateLimitedError(headers={"Retry-After": str(retry_after)})
