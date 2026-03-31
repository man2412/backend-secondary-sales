from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel


class PaginationMeta(BaseModel):
    page: int
    per_page: int
    total: int
    total_pages: int


class APIResponse(BaseModel):
    success: bool = True
    data: Any = None
    message: str = "Operation successful"
    pagination: PaginationMeta | None = None


def ok(
    data: Any = None,
    message: str = "Operation successful",
    pagination: PaginationMeta | None = None,
) -> dict[str, Any]:
    body = APIResponse(success=True, data=data, message=message, pagination=pagination).model_dump(
        exclude_none=True
    )
    return jsonable_encoder(body)


def err(message: str, data: Any = None) -> dict[str, Any]:
    body = APIResponse(success=False, data=data, message=message).model_dump(exclude_none=True)
    return jsonable_encoder(body)
