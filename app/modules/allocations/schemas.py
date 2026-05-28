import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Sent for legacy clients that validate `division_id` as a required UUID (null breaks Zod `.uuid()`).
_LEGACY_DIVISION_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


class HeadquarterAllocOut(BaseModel):
    id: uuid.UUID
    mr_id: uuid.UUID
    headquarter_id: uuid.UUID
    headquarter_name: str | None
    allocated_by: uuid.UUID
    allocated_at: datetime
    is_active: bool


class DoctorAllocOut(BaseModel):
    id: uuid.UUID
    mr_id: uuid.UUID
    doctor_id: uuid.UUID
    doctor_name: str | None
    # Legacy shape: doctor allocations are no longer per-division. Use placeholder UUID + empty name
    # so strict client schemas (e.g. z.string().uuid()) do not crash on null.
    division_id: uuid.UUID = Field(default=_LEGACY_DIVISION_ID)
    division_name: str = Field(default="")
    allocated_by: uuid.UUID
    allocated_at: datetime
    is_active: bool


class ProductAllocOut(BaseModel):
    """Legacy response shape; list is always empty — product allocations were removed."""

    id: uuid.UUID
    mr_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str | None = None
    allocated_by: uuid.UUID
    allocated_at: datetime
    is_active: bool


class StoreAllocOut(BaseModel):
    id: uuid.UUID
    mr_id: uuid.UUID
    medical_store_id: uuid.UUID
    store_name: str | None
    allocated_by: uuid.UUID
    allocated_at: datetime
    is_active: bool


class AllocationsBundleOut(BaseModel):
    headquarters: list[HeadquarterAllocOut]
    doctors: list[DoctorAllocOut]
    medical_stores: list[StoreAllocOut]
    products: list[ProductAllocOut] = Field(default_factory=list)


class HeadquarterAllocCreate(BaseModel):
    headquarter_id: uuid.UUID


class DoctorAllocCreate(BaseModel):
    doctor_id: uuid.UUID


class StoreAllocCreate(BaseModel):
    medical_store_id: uuid.UUID


class AllocationOps(BaseModel):
    model_config = ConfigDict(extra="ignore")

    add_headquarters: list[uuid.UUID] = Field(default_factory=list)
    remove_headquarter_alloc_ids: list[uuid.UUID] = Field(default_factory=list)
    add_doctors: list[DoctorAllocCreate] = Field(default_factory=list)
    remove_doctor_alloc_ids: list[uuid.UUID] = Field(default_factory=list)
    add_stores: list[uuid.UUID] = Field(default_factory=list)
    remove_store_alloc_ids: list[uuid.UUID] = Field(default_factory=list)
