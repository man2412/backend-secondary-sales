from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/aptus"
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    # Legacy symmetric secret (HS256). Required only while tokens use the legacy signing key.
    SUPABASE_JWT_SECRET: str = ""
    # How long to cache JWKS for ES256 verification (seconds). Supabase edge caches ~10m.
    SUPABASE_JWKS_CACHE_SECONDS: int = 600
    REDIS_URL: str = "redis://localhost:6379/0"
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me"
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    API_V1_PREFIX: str = "/v1"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def supabase_jwks_url(self) -> str:
        base = self.SUPABASE_URL.rstrip("/")
        return f"{base}/auth/v1/.well-known/jwks.json"


settings = Settings()
