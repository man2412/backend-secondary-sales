from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_token_payload
from app.core.database import get_db
from app.core.responses import ok
from app.modules.auth.schemas import SyncUserResponse
from app.modules.auth.service import AuthService
from app.modules.users.schemas import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/sync-user")
async def sync_user(
    payload: Annotated[dict, Depends(get_token_payload)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    try:
        user, linked = await AuthService().sync_user(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    body = SyncUserResponse(user=UserOut.model_validate(user), linked=linked)
    return ok(data=body.model_dump(mode="json"), message="User synced" if linked else "Session OK")
