import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User


class UserRepository:
    async def get_by_supabase_id(self, db: AsyncSession, supabase_id: uuid.UUID) -> User | None:
        result = await db.execute(select(User).where(User.supabase_id == supabase_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, db: AsyncSession, email: str) -> User | None:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, db: AsyncSession, user_id: uuid.UUID) -> User | None:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def link_supabase_id(self, db: AsyncSession, user: User, supabase_id: uuid.UUID) -> User:
        user.supabase_id = supabase_id
        await db.flush()
        await db.refresh(user)
        return user

    async def list_mr_ids_under_manager(self, db: AsyncSession, manager_id: uuid.UUID) -> Sequence[uuid.UUID]:
        """All MR user IDs in the subtree below manager (recursive), including self if MR."""
        q = text(
            """
            WITH RECURSIVE subtree AS (
                SELECT id, role, reports_to FROM users WHERE id = :mid AND is_active = true
                UNION ALL
                SELECT u.id, u.role, u.reports_to
                FROM users u
                INNER JOIN subtree s ON u.reports_to = s.id
                WHERE u.is_active = true
            )
            SELECT id FROM subtree WHERE role = 'MR'
            """
        )
        result = await db.execute(q, {"mid": str(manager_id)})
        return [uuid.UUID(str(row[0])) for row in result.fetchall()]

    async def list_mr_ids_for_state_scope(
        self, db: AsyncSession, company_id: uuid.UUID, state_id: uuid.UUID
    ) -> Sequence[uuid.UUID]:
        """MRs whose user.state_id matches or who report up within same state (simplified: state_id on MR)."""
        result = await db.execute(
            select(User.id).where(
                User.company_id == company_id,
                User.role == UserRole.MR,
                User.is_active.is_(True),
                User.state_id == state_id,
            )
        )
        return [row[0] for row in result.fetchall()]

    async def list_mr_ids_for_company(self, db: AsyncSession, company_id: uuid.UUID) -> Sequence[uuid.UUID]:
        result = await db.execute(
            select(User.id).where(
                User.company_id == company_id,
                User.role == UserRole.MR,
                User.is_active.is_(True),
            )
        )
        return [row[0] for row in result.fetchall()]

    async def list_users(
        self,
        db: AsyncSession,
        *,
        company_id: uuid.UUID,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[User], int]:
        base = select(User).where(User.company_id == company_id)
        count_q = select(func.count()).select_from(User).where(User.company_id == company_id)
        if active_only:
            base = base.where(User.is_active.is_(True))
            count_q = count_q.where(User.is_active.is_(True))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(User.full_name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def patch_user(self, db: AsyncSession, user: User, patch: dict) -> User:
        if "division_id" in patch:
            user.division_id = patch["division_id"]
        if "employee_code" in patch:
            user.employee_code = patch["employee_code"]
        if "full_name" in patch:
            user.full_name = patch["full_name"]
        if "email" in patch:
            user.email = str(patch["email"])
        if "phone" in patch:
            user.phone = patch["phone"]
        if "role" in patch:
            user.role = patch["role"]
        if "reports_to" in patch:
            user.reports_to = patch["reports_to"]
        if "state_id" in patch:
            user.state_id = patch["state_id"]
        if "is_active" in patch:
            user.is_active = patch["is_active"]
        await db.flush()
        await db.refresh(user)
        return user
