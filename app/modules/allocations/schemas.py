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


class StoreAllocOut(BaseModel):
    id: uuid.UUID
    mr_id: uuid.UUID
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
    medical_stores: list[StoreAllocOut]
    products: list[ProductAllocOut]


class LocationAllocCreate(BaseModel):
    location_id: uuid.UUID


class DoctorAllocCreate(BaseModel):
    doctor_id: uuid.UUID
    division_id: uuid.UUID


class StoreAllocCreate(BaseModel):
    medical_store_id: uuid.UUID


class ProductAllocCreate(BaseModel):
    product_id: uuid.UUID


class AllocationOps(BaseModel):
    add_locations: list[uuid.UUID] = []
    remove_location_alloc_ids: list[uuid.UUID] = []
    add_doctors: list[DoctorAllocCreate] = []
    remove_doctor_alloc_ids: list[uuid.UUID] = []
    add_stores: list[uuid.UUID] = []
    remove_store_alloc_ids: list[uuid.UUID] = []
    add_products: list[uuid.UUID] = []
    remove_product_alloc_ids: list[uuid.UUID] = []
