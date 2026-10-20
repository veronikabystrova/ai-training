import time

from app.core.config import Settings
from app.core.security import PasswordHasher


async def test_argon2_verify_takes_at_least_100ms_with_production_params() -> None:
    s = Settings(
        database_url="postgresql+asyncpg://x", redis_url="redis://x", jwt_secret="j" * 32,
        hash_salt="s" * 16,
    )  # fmt: skip
    hasher = PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_kib=s.argon2_memory_kib,
        parallelism=s.argon2_parallelism,
    )
    digest = await hasher.hash("Correct-Horse-Battery-Staple-9")
    start = time.perf_counter()
    assert await hasher.verify(digest, "Correct-Horse-Battery-Staple-9")
    assert time.perf_counter() - start >= 0.1
