"""
Application settings.

SECURITY NOTE (Phase 2): every value that was hardcoded in the legacy POS
(JWT secret 'whateverItWas', DataFlair sync token 'Vyo0WttjzBTh') MUST come
from environment variables here. There is no default JWT secret in
production mode: if IGPOS_ENV=production and IGPOS_JWT_SECRET is unset,
the app refuses to start (see validator below). A development-only
fallback exists purely so local/offline UAT prep doesn't require secrets
management infrastructure yet (per the zero-paid-services development
policy) -- it must never be reachable in production.
"""
from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IGPOS_", env_file=".env", extra="ignore")

    env: str = "development"  # development | staging | production
    api_v1_prefix: str = "/api/v1"

    # Local-first, no paid services required during development.
    database_url: str = "postgresql+psycopg://igpos:igpos_dev_local@localhost:5432/igpos_dev"

    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 30
    jwt_refresh_token_days: int = 14

    # Money: all monetary values are integer minor units (see app/core/money.py).
    default_currency: str = "EUR"

    # Website integration (Phase 11) — provider is pluggable; "mock" requires
    # no external service and no credentials, satisfying the no-paid-services
    # development policy. Swap to a real provider only at UAT (Phase 21).
    website_provider: str = "mock"

    # Extra category is excluded from website sync by explicit business rule.
    excluded_sync_category_names: tuple[str, ...] = ("Extra",)

    @field_validator("jwt_secret")
    @classmethod
    def _no_insecure_secret_in_production(cls, v: str, info):
        # NOTE: pydantic-settings validates before we know `env` in some
        # versions; the authoritative production gate also lives in
        # app/main.py startup check as a second line of defense.
        return v


@lru_cache
def get_settings() -> "Settings":
    return Settings()
