from typing import Annotated
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_roles
from app.models.enums import UserRole
from app.core.database import get_db
from app.core.responses import PaginationMeta, ok
from app.models.user import User
from app.modules.sales.schemas import SecondarySaleCreate, SecondarySaleOut, SecondarySaleUpdate
from app.modules.sales.service import SalesService

router = APIRouter(prefix="/secondary-sales", tags=["secondary-sales"])


@router.get("")
async def list_secondary_sales(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    sale_date: Annotated[date | None, Query()] = None,
    mr_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    try:
        rows, total = await SalesService().list_sales(
            db,
            current,
            page=page,
            per_page=per_page,
            sale_date=sale_date,
            mr_id_filter=mr_id,
            include_inactive=include_inactive,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    total_pages = (total + per_page - 1) // per_page if total else 0
    data = [SecondarySaleOut.model_validate(r).model_dump(mode="json") for r in rows]
    return ok(
        data=data,
        pagination=PaginationMeta(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
        ),
    )


@router.get("/{sale_id}")
async def get_secondary_sale(
    sale_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await SalesService().get_sale(db, current, sale_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sale not found")
    return ok(data=SecondarySaleOut.model_validate(row).model_dump(mode="json"))


@router.post("")
async def create_secondary_sale(
    body: SecondarySaleCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        row = await SalesService().create_sale(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=SecondarySaleOut.model_validate(row).model_dump(mode="json"), message="Sale created")


@router.put("/{sale_id}")
async def update_secondary_sale(
    sale_id: UUID,
    body: SecondarySaleUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN))],
) -> dict:
    try:
        row = await SalesService().update_sale(db, current, sale_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=SecondarySaleOut.model_validate(row).model_dump(mode="json"), message="Sale updated")


@router.delete("/{sale_id}")
async def delete_secondary_sale(
    sale_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN))],
) -> dict:
    try:
        await SalesService().delete_sale(db, current, sale_id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(message="Sale removed")
