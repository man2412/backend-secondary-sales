from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# `.env` must load from the repo root, not the process cwd (uvicorn / IDE may start elsewhere).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        # Windows / shells often export GEMINI_API_KEY= (empty). Without this, that beats `.env`.
        env_ignore_empty=True,
    )

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
    MINERU_API_KEY: str = ""
    # Logging level for the app's own loggers (DEBUG | INFO | WARNING | ERROR).
    # Set to DEBUG when investigating slow imports / failures.
    LOG_LEVEL: str = "INFO"

    @field_validator("GEMINI_API_KEY", "OPENAI_API_KEY", "MINERU_API_KEY", mode="before")
    @classmethod
    def _normalize_api_key(cls, v: object) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
            s = s[1:-1]
        return s

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def supabase_jwks_url(self) -> str:
        base = self.SUPABASE_URL.rstrip("/")
        return f"{base}/auth/v1/.well-known/jwks.json"


settings = Settings()
