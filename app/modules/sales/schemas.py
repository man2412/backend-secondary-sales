import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Secondary Sale
# ---------------------------------------------------------------------------

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
    pts: float | None
    mrp: float
    special_price: float | None
    total_amount: float | None
    reported_amount: float | None
    bill_ref: str | None
    batch: str | None
    pack: str | None
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


# ---------------------------------------------------------------------------
# Import Job
# ---------------------------------------------------------------------------

class ImportJobOut(BaseModel):
    model_config = {"from_attributes": True, "protected_namespaces": ()}

    id: uuid.UUID
    filename: str
    source_type: str
    status: str
    mr_id: uuid.UUID | None
    detected_fos_name: str | None
    total_rows: int | None
    committed_count: int | None
    model_used: str | None
    chunks_total: int | None = None
    chunks_succeeded: int | None = None
    extraction_warnings: list[str] | None = None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ImportJobPreviewOut(BaseModel):
    model_config = {"from_attributes": True, "protected_namespaces": ()}

    id: uuid.UUID
    filename: str
    source_type: str
    status: str
    mr_id: uuid.UUID | None
    detected_fos_name: str | None
    total_rows: int | None
    model_used: str | None
    chunks_total: int | None = None
    chunks_succeeded: int | None = None
    extraction_warnings: list[str] | None = None
    structured_rows: list[Any] | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ImportJobCommitBody(BaseModel):
    """Frontend sends back the (possibly edited) rows for final commit."""
    confirmed_rows: list[dict[str, Any]] = Field(
        ...,
        description="The structured_rows array from preview, after user edits. Rows with skip=true are excluded.",
    )
