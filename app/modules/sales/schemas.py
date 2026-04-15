import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class SecondarySaleOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    mr_id: uuid.UUID
    product_id: uuid.UUID
    doctor_id: uuid.UUID | None
    medical_store_id: uuid.UUID | None
    division_id: uuid.UUID
    headquarter_id: uuid.UUID
    location_id: uuid.UUID
    state_id: uuid.UUID
    sale_date: date
    sale_qty: int
    free_qty: int
    ptr: float
    pts: float
    mrp: float
    special_price: float | None
    total_amount: float | None
    remarks: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SecondarySaleCreate(BaseModel):
    """As MR, omit `mr_id` (defaults to you). As SUPER_ADMIN, set `mr_id` to the selling MR."""

    mr_id: uuid.UUID | None = None
    product_id: uuid.UUID
    doctor_id: uuid.UUID | None = None
    medical_store_id: uuid.UUID | None = None
    location_id: uuid.UUID
    sale_date: date
    sale_qty: int = Field(..., ge=1)
    free_qty: int = Field(default=0, ge=0)
    special_price: float | None = Field(
        default=None,
        ge=0,
        description="Unit price override; omit or null to use PTR. Sending 0 is stored as null (PTR).",
    )
    remarks: str | None = None


class SecondarySaleUpdate(BaseModel):
    sale_qty: int | None = Field(None, ge=0)
    free_qty: int | None = Field(None, ge=0)
    special_price: float | None = Field(
        None,
        ge=0,
        description="0 is stored as null (revert to PTR for generated total_amount).",
    )
    remarks: str | None = None
