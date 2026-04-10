import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserCreate, UserUpdate


class UserService:
    def __init__(self, repo: UserRepository | None = None) -> None:
        self._repo = repo or UserRepository()

    async def create_user(self, db: AsyncSession, data: UserCreate, *, actor_role: UserRole) -> User:
        if actor_role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only SUPER_ADMIN can create users")
        sid = data.supabase_id if data.supabase_id is not None else uuid.uuid4()
        user = User(
            supabase_id=sid,
            company_id=data.company_id,
            division_id=data.division_id,
            employee_code=data.employee_code,
            full_name=data.full_name,
            email=str(data.email),
            phone=data.phone,
            role=data.role,
            reports_to=data.reports_to,
            state_id=data.state_id,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    async def get_visible_mr_ids(self, db: AsyncSession, user: User) -> list[uuid.UUID]:
        if user.role == UserRole.MR:
            return [user.id]
        if user.role == UserRole.SUPER_ADMIN:
            r = await db.execute(
                select(User.id).where(User.role == UserRole.MR, User.is_active.is_(True))
            )
            return [row[0] for row in r.fetchall()]
        if user.role in (UserRole.SALES_DIRECTOR,):
            return list(await self._repo.list_mr_ids_for_company(db, user.company_id))
        if user.role in (UserRole.STATE_HEAD, UserRole.RSM, UserRole.DEPUTY_RSM) and user.state_id:
            return list(
                await self._repo.list_mr_ids_for_state_scope(db, user.company_id, user.state_id)
            )
        if user.role == UserRole.ASM:
            return list(await self._repo.list_mr_ids_under_manager(db, user.id))
        return []

    async def list_company_users(
        self,
        db: AsyncSession,
        user: User,
        *,
        company_id_query: uuid.UUID | None,
        q: str | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list[User], int]:
        if user.role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only SUPER_ADMIN can list company users")
        if company_id_query is None:
            raise ValueError("company_id is required for SUPER_ADMIN")
        company_id = company_id_query
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_users(
            db,
            company_id=company_id,
            q=q,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def update_user(self, db: AsyncSession, actor: User, target_id: uuid.UUID, body: UserUpdate) -> User:
        if actor.role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only SUPER_ADMIN can update users")
        target = await self._repo.get_by_id(db, target_id)
        if target is None:
            raise ValueError("User not found")
        data = body.model_dump(exclude_unset=True)
        if not data:
            raise ValueError("No fields to update")
        if "email" in data:
            other = await self._repo.get_by_email(db, str(data["email"]))
            if other is not None and other.id != target.id:
                raise ValueError("Email already in use")
        if "reports_to" in data:
            rto = data["reports_to"]
            if rto is not None:
                mgr = await self._repo.get_by_id(db, rto)
                if mgr is None or mgr.company_id != target.company_id:
                    raise ValueError("reports_to must be a user in the same company")
                if mgr.id == target.id:
                    raise ValueError("Cannot report to self")
        return await self._repo.patch_user(db, target, data)

    async def delete_user(self, db: AsyncSession, actor: User, target_id: uuid.UUID) -> User:
        if actor.role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only SUPER_ADMIN can delete users")
        target = await self._repo.get_by_id(db, target_id)
        if target is None:
            raise ValueError("User not found")
        if not target.is_active:
            return target
        return await self._repo.patch_user(db, target, {"is_active": False})
