"""Supabase Storage wrapper — Supabase SDK allowed only here per project rules."""

from app.core.config import settings


def get_storage_public_url(bucket: str, path: str) -> str:
    base = settings.SUPABASE_URL.rstrip("/")
    return f"{base}/storage/v1/object/public/{bucket}/{path}"
