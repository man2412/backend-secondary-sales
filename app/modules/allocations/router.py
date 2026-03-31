from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.responses import ok
from app.models.user import User
from app.modules.allocations.schemas import DoctorAllocCreate, LocationAllocCreate, ProductAllocCreate
from app.modules.allocations.service import AllocationsService

router = APIRouter(prefix="/allocations", tags=["allocations"])


@router.get("/mr/{mr_id}")
async def get_mr_allocations(
    mr_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    try:
        bundle = await AllocationsService().get_bundle(
            db, current, mr_id, include_inactive=include_inactive
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=bundle.model_dump(mode="json"))


@router.post("/mr/{mr_id}/locations")
async def post_mr_location_alloc(
    mr_id: UUID,
    body: LocationAllocCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        out = await AllocationsService().add_location(db, current, mr_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=out.model_dump(mode="json"), message="Allocated")


@router.post("/mr/{mr_id}/doctors")
async def post_mr_doctor_alloc(
    mr_id: UUID,
    body: DoctorAllocCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        out = await AllocationsService().add_doctor(db, current, mr_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=out.model_dump(mode="json"), message="Allocated")


@router.post("/mr/{mr_id}/products")
async def post_mr_product_alloc(
    mr_id: UUID,
    body: ProductAllocCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        out = await AllocationsService().add_product(db, current, mr_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=out.model_dump(mode="json"), message="Allocated")


@router.delete("/locations/{alloc_id}")
async def delete_location_alloc(
    alloc_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        await AllocationsService().delete_location(db, current, alloc_id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(message="Allocation removed")


@router.delete("/doctors/{alloc_id}")
async def delete_doctor_alloc(
    alloc_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        await AllocationsService().delete_doctor(db, current, alloc_id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(message="Allocation removed")


@router.delete("/products/{alloc_id}")
async def delete_product_alloc(
    alloc_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        await AllocationsService().delete_product(db, current, alloc_id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(message="Allocation removed")
