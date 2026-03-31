import uuid
from datetime import datetime

from pydantic import BaseModel


class LocationAllocOut(BaseModel):
    id: uuid.UUID
    mr_id: uuid.UUID
    location_id: uuid.UUID
    location_name: str | None
    allocated_by: uuid.UUID
    allocated_at: datetime
    is_active: bool


class DoctorAllocOut(BaseModel):
    id: uuid.UUID
    mr_id: uuid.UUID
    doctor_id: uuid.UUID
    doctor_name: str | None
    division_id: uuid.UUID
    division_name: str | None
    allocated_by: uuid.UUID
    allocated_at: datetime
    is_active: bool


class MedicalStoreViaDoctorOut(BaseModel):
    """Store reachable for the MR via an allocated doctor (doctor_medical_stores), not a direct MR–store row."""

    mr_doctor_allocation_id: uuid.UUID
    doctor_id: uuid.UUID
    doctor_name: str | None
    division_id: uuid.UUID
    division_name: str | None
    medical_store_id: uuid.UUID
    store_name: str | None
    allocated_by: uuid.UUID
    allocated_at: datetime
    is_active: bool


class ProductAllocOut(BaseModel):
    id: uuid.UUID
    mr_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str | None
    allocated_by: uuid.UUID
    allocated_at: datetime
    is_active: bool


class AllocationsBundleOut(BaseModel):
    locations: list[LocationAllocOut]
    doctors: list[DoctorAllocOut]
    medical_stores: list[MedicalStoreViaDoctorOut]
    products: list[ProductAllocOut]


class LocationAllocCreate(BaseModel):
    location_id: uuid.UUID


class DoctorAllocCreate(BaseModel):
    doctor_id: uuid.UUID
    division_id: uuid.UUID


class ProductAllocCreate(BaseModel):
    product_id: uuid.UUID
