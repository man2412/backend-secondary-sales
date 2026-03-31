from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_roles
from app.core.database import get_db
from app.core.responses import PaginationMeta, ok
from app.models.enums import UserRole
from app.models.user import User
from app.modules.stockists.schemas import (
    MedicalStoreCreate,
    MedicalStoreOut,
    MedicalStoreUpdate,
    StockistCreate,
    StockistOut,
    StockistUpdate,
    SuperStockistCreate,
    SuperStockistOut,
    SuperStockistUpdate,
)
from app.modules.stockists.service import StockistsService

router = APIRouter(tags=["stockists"])

_super_admin = require_roles(UserRole.SUPER_ADMIN)


def _paginated(items: list, *, page: int, per_page: int, total: int, out_cls: type) -> dict:
    total_pages = (total + per_page - 1) // per_page if total else 0
    data = [out_cls.model_validate(x).model_dump(mode="json") for x in items]
    return ok(
        data=data,
        pagination=PaginationMeta(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
        ),
    )


# --- Super stockists ---


@router.get("/super-stockists")
async def list_super_stockists(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    company_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    svc = StockistsService()
    rows, total = await svc.list_super_stockists(
        db,
        current,
        company_id_query=company_id,
        page=page,
        per_page=per_page,
        include_inactive=include_inactive,
    )
    return _paginated(rows, page=page, per_page=per_page, total=total, out_cls=SuperStockistOut)


@router.get("/super-stockists/{entity_id}")
async def get_super_stockist(
    entity_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await StockistsService().get_super_stockist(db, current, entity_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Super stockist not found")
    return ok(data=SuperStockistOut.model_validate(row).model_dump(mode="json"))


@router.post("/super-stockists")
async def create_super_stockist(
    body: SuperStockistCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await StockistsService().create_super_stockist(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=SuperStockistOut.model_validate(row).model_dump(mode="json"), message="Created")


@router.put("/super-stockists/{entity_id}")
async def update_super_stockist(
    entity_id: UUID,
    body: SuperStockistUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await StockistsService().update_super_stockist(db, current, entity_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=SuperStockistOut.model_validate(row).model_dump(mode="json"), message="Updated")


# --- Stockists ---


@router.get("/stockists")
async def list_stockists(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    company_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    svc = StockistsService()
    rows, total = await svc.list_stockists(
        db,
        current,
        company_id_query=company_id,
        page=page,
        per_page=per_page,
        include_inactive=include_inactive,
    )
    return _paginated(rows, page=page, per_page=per_page, total=total, out_cls=StockistOut)


@router.get("/stockists/{entity_id}")
async def get_stockist(
    entity_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await StockistsService().get_stockist(db, current, entity_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stockist not found")
    return ok(data=StockistOut.model_validate(row).model_dump(mode="json"))


@router.post("/stockists")
async def create_stockist(
    body: StockistCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await StockistsService().create_stockist(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=StockistOut.model_validate(row).model_dump(mode="json"), message="Created")


@router.put("/stockists/{entity_id}")
async def update_stockist(
    entity_id: UUID,
    body: StockistUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await StockistsService().update_stockist(db, current, entity_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=StockistOut.model_validate(row).model_dump(mode="json"), message="Updated")


# --- Medical stores ---


@router.get("/medical-stores")
async def list_medical_stores(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    company_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    svc = StockistsService()
    rows, total = await svc.list_medical_stores(
        db,
        current,
        company_id_query=company_id,
        page=page,
        per_page=per_page,
        include_inactive=include_inactive,
    )
    return _paginated(rows, page=page, per_page=per_page, total=total, out_cls=MedicalStoreOut)


@router.get("/medical-stores/{entity_id}")
async def get_medical_store(
    entity_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await StockistsService().get_medical_store(db, current, entity_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Medical store not found")
    return ok(data=MedicalStoreOut.model_validate(row).model_dump(mode="json"))


@router.post("/medical-stores")
async def create_medical_store(
    body: MedicalStoreCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        row = await StockistsService().create_medical_store(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=MedicalStoreOut.model_validate(row).model_dump(mode="json"), message="Created")


@router.put("/medical-stores/{entity_id}")
async def update_medical_store(
    entity_id: UUID,
    body: MedicalStoreUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        row = await StockistsService().update_medical_store(db, current, entity_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=MedicalStoreOut.model_validate(row).model_dump(mode="json"), message="Updated")
