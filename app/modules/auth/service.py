from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.modules.users.repository import UserRepository


class AuthService:
    def __init__(self, repo: UserRepository | None = None) -> None:
        self._repo = repo or UserRepository()

    async def sync_user(self, db: AsyncSession, payload: dict) -> tuple[User, bool]:
        sub = payload.get("sub")
        email = (payload.get("email") or "").strip().lower()
        if not email:
            meta = payload.get("user_metadata") or {}
            if isinstance(meta, dict):
                email = (meta.get("email") or "").strip().lower()
        if not sub or not email:
            raise ValueError("Token must include sub and email")

        supabase_id = UUID(sub)

        existing = await self._repo.get_by_supabase_id(db, supabase_id)
        if existing:
            return existing, False

        by_email = await self._repo.get_by_email(db, email)
        if by_email:
            linked = await self._repo.link_supabase_id(db, by_email, supabase_id)
            return linked, True

        raise LookupError(
            "No provisioned user found for this email. Ask an administrator to create your account."
        )
