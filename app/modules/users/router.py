from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_roles
from app.core.database import get_db
from app.core.responses import PaginationMeta, ok
from app.models.enums import UserRole
from app.models.user import User
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserCreate, UserOut, UserUpdate
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_direct_reports(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    result = await db.execute(select(User).where(User.reports_to == current.id, User.is_active.is_(True)))
    rows = result.scalars().all()
    return ok(data=[UserOut.model_validate(u).model_dump(mode="json") for u in rows])


@router.get("/company")
async def list_company_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(description="Search: name/email/phone/employee_code")] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    rows, total = await UserService().list_company_users(
        db,
        current,
        q=q,
        page=page,
        per_page=per_page,
        include_inactive=include_inactive,
    )
    total_pages = (total + per_page - 1) // per_page if per_page else 1
    return ok(
        data=[UserOut.model_validate(u).model_dump(mode="json") for u in rows],
        pagination=PaginationMeta(page=page, per_page=per_page, total=total, total_pages=total_pages),
    )


@router.post("")
async def create_user(
    body: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN))],
) -> dict:
    try:
        user = await UserService().create_user(db, body, actor_role=current.role)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=UserOut.model_validate(user).model_dump(mode="json"), message="User created")


@router.get("/hierarchy")
async def get_hierarchy(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Returns subtree of users under current user (active only), flat list with depth via BFS."""
    from sqlalchemy import text

    q = text(
        """
        WITH RECURSIVE subtree AS (
            SELECT id, full_name, email, role, reports_to, 0 AS depth
            FROM users WHERE id = :root AND is_active = true
            UNION ALL
            SELECT u.id, u.full_name, u.email, u.role, u.reports_to, s.depth + 1
            FROM users u
            INNER JOIN subtree s ON u.reports_to = s.id
            WHERE u.is_active = true
        )
        SELECT id, full_name, email, role, reports_to, depth FROM subtree ORDER BY depth, full_name
        """
    )
    r = await db.execute(q, {"root": str(current.id)})
    data: list[dict] = []
    for row in r.mappings():
        role = row["role"]
        role_str = role.value if hasattr(role, "value") else str(role)
        rto = row["reports_to"]
        data.append(
            {
                "id": str(row["id"]),
                "full_name": row["full_name"],
                "email": row["email"],
                "role": role_str,
                "reports_to": str(rto) if rto is not None else None,
                "depth": int(row["depth"]),
            }
        )
    return ok(data=data)


@router.get("/{user_id}")
async def get_user(
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    repo = UserRepository()
    user = await repo.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return ok(data=UserOut.model_validate(user).model_dump(mode="json"))


@router.put("/{user_id}")
async def update_user(
    user_id: UUID,
    body: UserUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        user = await UserService().update_user(db, current, user_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=UserOut.model_validate(user).model_dump(mode="json"), message="User updated")


@router.delete("/{user_id}")
async def delete_user(
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        user = await UserService().delete_user(db, current, user_id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=UserOut.model_validate(user).model_dump(mode="json"), message="User deleted")


@router.get("/{user_id}/reporting-chain")
async def get_reporting_chain(
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    repo = UserRepository()
    user = await repo.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    chain = await repo.reporting_chain(db, user_id)
    return ok(data=chain)
