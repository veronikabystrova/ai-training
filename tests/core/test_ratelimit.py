import pytest
import structlog.testing

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


async def test_redis_outage_fails_open_and_logs_once() -> None:
    # Port 1 is never listening: connection refused. Architecture: rate_limit is fail-open.
    rl = RateLimiter("redis://127.0.0.1:1")
    with structlog.testing.capture_logs() as logs:
        await rl.hit(RouteClass.login_ip, "user@example.com")  # must not raise
    warnings = [e for e in logs if e["event"] == "rate_limit_unavailable"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["route_class"] == "login_ip"
    assert "user@example.com" not in str(logs)  # no key / PII in the log


async def test_real_limit_still_raises_after_outage_handling(redis_url: str) -> None:
    rl = RateLimiter(redis_url)
    for _ in range(10):
        await rl.hit(RouteClass.login_ip, "z")
    with pytest.raises(RateLimitedError):
        await rl.hit(RouteClass.login_ip, "z")
