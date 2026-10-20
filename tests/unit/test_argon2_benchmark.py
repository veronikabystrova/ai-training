from argon2 import extract_parameters
from argon2.low_level import Type

from app.core.config import Settings
from app.core.security import PasswordHasher


async def _assert_spec_params(hasher: PasswordHasher) -> None:
    digest = await hasher.hash("Correct-Horse-Battery-Staple-9")
    assert digest.startswith("$argon2id$")
    params = extract_parameters(digest)
    assert params.type is Type.ID
    assert (params.time_cost, params.memory_cost, params.parallelism) == (3, 65536, 4)


async def test_settings_defaults_produce_spec_argon2_parameters() -> None:
    s = Settings(
        database_url="postgresql+asyncpg://x", redis_url="redis://x", jwt_secret="j" * 32,
        hash_salt="s" * 16,
    )  # fmt: skip
    await _assert_spec_params(
        PasswordHasher(
            time_cost=s.argon2_time_cost,
            memory_kib=s.argon2_memory_kib,
            parallelism=s.argon2_parallelism,
        )
    )


async def test_password_hasher_defaults_produce_spec_argon2_parameters() -> None:
    await _assert_spec_params(PasswordHasher())
