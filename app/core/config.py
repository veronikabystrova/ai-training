from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_ott_ttl_hours() -> dict[str, int]:
    return {"trainer_setup": 24, "password_reset": 1, "email_verify": 24, "email_change": 24}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=None, extra="ignore")

    database_url: str
    redis_url: str
    jwt_secret: SecretStr = Field(min_length=32)
    access_ttl_seconds: int = 900
    refresh_ttl_days: int = 30
    impersonation_max_minutes: int = 60
    ott_ttl_hours: dict[str, int] = Field(default_factory=_default_ott_ttl_hours)
    coach_link_ttl_days: int = 7
    approval_ttl_hours: int = 48
    idempotency_ttl_hours: int = 24
    erasure_retention_years: int = 7
    hash_salt: SecretStr = Field(min_length=16)  # salt for ip/user-agent hashes; no default
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4
    public_base_url: str = "https://app.example.com"
    s3_bucket: str = "app-media"


@lru_cache
def get_settings() -> Settings:
    return Settings()
