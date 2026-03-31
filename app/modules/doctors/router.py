from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.responses import PaginationMeta, ok
from app.models.user import User
from app.modules.doctors.schemas import DoctorCreate, DoctorUpdate
from app.modules.doctors.service import DoctorsService

router = APIRouter(prefix="/doctors", tags=["doctors"])


def _paginated(items: list, *, page: int, per_page: int, total: int) -> dict:
    total_pages = (total + per_page - 1) // per_page if total else 0
    data = [x.model_dump(mode="json") for x in items]
    return ok(
        data=data,
        pagination=PaginationMeta(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
        ),
    )


@router.get("")
async def list_doctors(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    company_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    svc = DoctorsService()
    rows, total = await svc.list_doctors(
        db,
        current,
        company_id_query=company_id,
        page=page,
        per_page=per_page,
        include_inactive=include_inactive,
    )
    return _paginated(rows, page=page, per_page=per_page, total=total)


@router.get("/{doctor_id}")
async def get_doctor(
    doctor_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await DoctorsService().get_doctor(db, current, doctor_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    return ok(data=row.model_dump(mode="json"))


@router.post("")
async def create_doctor(
    body: DoctorCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        row = await DoctorsService().create_doctor(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=row.model_dump(mode="json"), message="Created")


@router.put("/{doctor_id}")
async def update_doctor(
    doctor_id: UUID,
    body: DoctorUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        row = await DoctorsService().update_doctor(db, current, doctor_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=row.model_dump(mode="json"), message="Updated")
