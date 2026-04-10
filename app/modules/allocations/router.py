from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.responses import ok
from app.models.user import User
from app.modules.allocations.schemas import AllocationOps
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


@router.put("/mr/{mr_id}")
async def apply_allocation_ops(
    mr_id: UUID,
    body: AllocationOps,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        bundle = await AllocationsService().apply_ops(db, current, mr_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=bundle.model_dump(mode="json"), message="Updated")
