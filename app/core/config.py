from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Frontend (SPA) routes that consume each one-time token; the SPA owns these paths.
SETUP_ROUTE = "/setup-password"
VERIFY_EMAIL_ROUTE = "/verify-email"
RESET_ROUTE = "/reset-password"
CONFIRM_EMAIL_CHANGE_ROUTE = "/confirm-email-change"


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
    argon2_parallelism: int = 4  # architecture spec: argon2id t=3, m=64 MiB, p=4
    # Frontend origin: share-link URLs and the links in emails (the "frontend_base_url").
    public_base_url: str = "https://app.example.com"
    s3_bucket: str = "app-media"
    media_base_url: str = "https://media.example.com"  # public origin serving stored keys
    presign_ttl_seconds: int = 300
    # RouteClass name -> limit string, e.g. {"default_principal": "5/minute"}; empty in production.
    rate_limit_overrides: dict[str, str] = Field(default_factory=dict)

    def frontend_url(self, path: str, *, token: str) -> str:
        """Link to a frontend route that consumes a one-time token."""
        return f"{self.public_base_url.rstrip('/')}{path}?token={token}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
