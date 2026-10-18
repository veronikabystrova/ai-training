import pytest

from app.core.errors import RateLimitedError
from app.core.ratelimit import RateLimiter, RouteClass


async def test_login_ip_limit_10_per_minute(redis_url: str) -> None:
    rl = RateLimiter(redis_url)
    for _ in range(10):
        await rl.hit(RouteClass.login_ip, "1.2.3.4")
    with pytest.raises(RateLimitedError) as e:
        await rl.hit(RouteClass.login_ip, "1.2.3.4")
    assert int(e.value.headers["Retry-After"]) >= 1


async def test_keys_are_independent(redis_url: str) -> None:
    rl = RateLimiter(redis_url)
    for _ in range(10):
        await rl.hit(RouteClass.login_ip, "a")
    await rl.hit(RouteClass.login_ip, "b")  # no raise


async def test_aclose_releases_connection(redis_url: str) -> None:
    rl = RateLimiter(redis_url)
    await rl.hit(RouteClass.login_ip, "c")
    await rl.aclose()
