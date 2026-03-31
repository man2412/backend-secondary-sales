import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DoctorOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    company_id: uuid.UUID
    full_name: str
    specialization: str | None
    qualification: str | None
    phone: str | None
    address: str | None
    location_id: uuid.UUID | None
    is_active: bool
    medical_store_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DoctorCreate(BaseModel):
    company_id: uuid.UUID | None = None
    full_name: str = Field(..., min_length=1, max_length=255)
    specialization: str | None = None
    qualification: str | None = None
    phone: str | None = None
    address: str | None = None
    location_id: uuid.UUID | None = None
    medical_store_ids: list[uuid.UUID] = Field(default_factory=list)


class DoctorUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=255)
    specialization: str | None = None
    qualification: str | None = None
    phone: str | None = None
    address: str | None = None
    location_id: uuid.UUID | None = None
    is_active: bool | None = None
    medical_store_ids: list[uuid.UUID] | None = None
