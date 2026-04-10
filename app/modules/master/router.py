from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_roles
from app.core.database import get_db
from app.core.responses import PaginationMeta, ok
from app.models.enums import UserRole
from app.models.user import User
from app.modules.master.schemas import (
    DivisionCreate,
    DivisionOut,
    DivisionUpdate,
    HeadquarterCreate,
    HeadquarterOut,
    HeadquarterUpdate,
    LocationCreate,
    LocationOut,
    LocationUpdate,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    StateCreate,
    StateOut,
    StateUpdate,
)
from app.modules.master.service import MasterService

router = APIRouter(tags=["master"])

_super_admin = require_roles(UserRole.SUPER_ADMIN)


async def _catch_list_scope(awaitable):
    try:
        return await awaitable
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


def _paginated(
    items: list,
    *,
    page: int,
    per_page: int,
    total: int,
    out_cls: type,
) -> dict:
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


# --- States ---


@router.get("/states")
async def list_states(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    company_id: Annotated[UUID | None, Query()] = None,
    q: Annotated[str | None, Query(description="Search by name/code")] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    svc = MasterService()
    rows, total = await _catch_list_scope(
        svc.list_states(
            db,
            current,
            company_id_query=company_id,
            q=q,
            page=page,
            per_page=per_page,
            include_inactive=include_inactive,
        )
    )
    return _paginated(rows, page=page, per_page=per_page, total=total, out_cls=StateOut)


@router.get("/states/{state_id}")
async def get_state(
    state_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await MasterService().get_state(db, current, state_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="State not found")
    return ok(data=StateOut.model_validate(row).model_dump(mode="json"))


@router.post("/states")
async def create_state(
    body: StateCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().create_state(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    return ok(data=StateOut.model_validate(row).model_dump(mode="json"), message="State created")


@router.put("/states/{state_id}")
async def update_state(
    state_id: UUID,
    body: StateUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_state(db, current, state_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=StateOut.model_validate(row).model_dump(mode="json"), message="State updated")


@router.delete("/states/{state_id}")
async def delete_state(
    state_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_state(db, current, state_id, StateUpdate(is_active=False))
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=StateOut.model_validate(row).model_dump(mode="json"), message="Deleted")


# --- Divisions ---


@router.get("/divisions")
async def list_divisions(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    company_id: Annotated[UUID | None, Query()] = None,
    q: Annotated[str | None, Query(description="Search by name")] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    rows, total = await _catch_list_scope(
        MasterService().list_divisions(
            db,
            current,
            company_id_query=company_id,
            q=q,
            page=page,
            per_page=per_page,
            include_inactive=include_inactive,
        )
    )
    return _paginated(rows, page=page, per_page=per_page, total=total, out_cls=DivisionOut)


@router.get("/divisions/{division_id}")
async def get_division(
    division_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await MasterService().get_division(db, current, division_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Division not found")
    return ok(data=DivisionOut.model_validate(row).model_dump(mode="json"))


@router.post("/divisions")
async def create_division(
    body: DivisionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().create_division(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    return ok(data=DivisionOut.model_validate(row).model_dump(mode="json"), message="Division created")


@router.put("/divisions/{division_id}")
async def update_division(
    division_id: UUID,
    body: DivisionUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_division(db, current, division_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=DivisionOut.model_validate(row).model_dump(mode="json"), message="Division updated")


@router.delete("/divisions/{division_id}")
async def delete_division(
    division_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_division(db, current, division_id, DivisionUpdate(is_active=False))
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=DivisionOut.model_validate(row).model_dump(mode="json"), message="Deleted")


# --- Headquarters ---


@router.get("/headquarters")
async def list_headquarters(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    company_id: Annotated[UUID | None, Query()] = None,
    q: Annotated[str | None, Query(description="Search by name")] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    rows, total = await _catch_list_scope(
        MasterService().list_headquarters(
            db,
            current,
            company_id_query=company_id,
            q=q,
            page=page,
            per_page=per_page,
            include_inactive=include_inactive,
        )
    )
    return _paginated(rows, page=page, per_page=per_page, total=total, out_cls=HeadquarterOut)


@router.get("/headquarters/{hq_id}")
async def get_headquarter(
    hq_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await MasterService().get_headquarter(db, current, hq_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Headquarter not found")
    return ok(data=HeadquarterOut.model_validate(row).model_dump(mode="json"))


@router.post("/headquarters")
async def create_headquarter(
    body: HeadquarterCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().create_headquarter(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=HeadquarterOut.model_validate(row).model_dump(mode="json"), message="Headquarter created")


@router.put("/headquarters/{hq_id}")
async def update_headquarter(
    hq_id: UUID,
    body: HeadquarterUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_headquarter(db, current, hq_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=HeadquarterOut.model_validate(row).model_dump(mode="json"), message="Headquarter updated")


@router.delete("/headquarters/{hq_id}")
async def delete_headquarter(
    hq_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_headquarter(db, current, hq_id, HeadquarterUpdate(is_active=False))
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=HeadquarterOut.model_validate(row).model_dump(mode="json"), message="Deleted")


# --- Locations ---


@router.get("/locations")
async def list_locations(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    company_id: Annotated[UUID | None, Query()] = None,
    q: Annotated[str | None, Query(description="Search by name")] = None,
    headquarter_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    rows, total = await _catch_list_scope(
        MasterService().list_locations(
            db,
            current,
            company_id_query=company_id,
            q=q,
            headquarter_id=headquarter_id,
            page=page,
            per_page=per_page,
            include_inactive=include_inactive,
        )
    )
    return _paginated(rows, page=page, per_page=per_page, total=total, out_cls=LocationOut)


@router.get("/locations/{location_id}")
async def get_location(
    location_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await MasterService().get_location(db, current, location_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    return ok(data=LocationOut.model_validate(row).model_dump(mode="json"))


@router.post("/locations")
async def create_location(
    body: LocationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().create_location(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=LocationOut.model_validate(row).model_dump(mode="json"), message="Location created")


@router.put("/locations/{location_id}")
async def update_location(
    location_id: UUID,
    body: LocationUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_location(db, current, location_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=LocationOut.model_validate(row).model_dump(mode="json"), message="Location updated")


@router.delete("/locations/{location_id}")
async def delete_location(
    location_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_location(db, current, location_id, LocationUpdate(is_active=False))
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=LocationOut.model_validate(row).model_dump(mode="json"), message="Deleted")


# --- Products ---


@router.get("/products")
async def list_products(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    company_id: Annotated[UUID | None, Query()] = None,
    q: Annotated[str | None, Query(description="Search by name")] = None,
    division_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    rows, total = await _catch_list_scope(
        MasterService().list_products(
            db,
            current,
            company_id_query=company_id,
            q=q,
            division_id=division_id,
            page=page,
            per_page=per_page,
            include_inactive=include_inactive,
        )
    )
    return _paginated(rows, page=page, per_page=per_page, total=total, out_cls=ProductOut)


@router.get("/products/{product_id}")
async def get_product(
    product_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    row = await MasterService().get_product(db, current, product_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return ok(data=ProductOut.model_validate(row).model_dump(mode="json"))


@router.post("/products")
async def create_product(
    body: ProductCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().create_product(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=ProductOut.model_validate(row).model_dump(mode="json"), message="Product created")


@router.put("/products/{product_id}")
async def update_product(
    product_id: UUID,
    body: ProductUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_product(db, current, product_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=ProductOut.model_validate(row).model_dump(mode="json"), message="Product updated")


@router.delete("/products/{product_id}")
async def delete_product(
    product_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(_super_admin)],
) -> dict:
    try:
        row = await MasterService().update_product(db, current, product_id, ProductUpdate(is_active=False))
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=ProductOut.model_validate(row).model_dump(mode="json"), message="Deleted")
