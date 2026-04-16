import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# --- State ---


class StateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    code: str | None = Field(None, max_length=10)


class StateUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    code: str | None = Field(None, max_length=10)
    is_active: bool | None = None


class StateOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    code: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Division ---


class DivisionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=50)


class DivisionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    code: str | None = Field(None, max_length=50)
    is_active: bool | None = None


class DivisionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    code: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Headquarter ---


class HeadquarterCreate(BaseModel):
    state_id: uuid.UUID
    division_ids: list[uuid.UUID]
    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=50)


class HeadquarterUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    code: str | None = Field(None, max_length=50)
    is_active: bool | None = None


class HeadquarterOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    state_id: uuid.UUID
    division_ids: list[uuid.UUID]
    name: str
    code: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Location ---


class LocationCreate(BaseModel):
    headquarter_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=50)


class LocationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    code: str | None = Field(None, max_length=50)
    is_active: bool | None = None


class LocationOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    headquarter_id: uuid.UUID
    name: str
    code: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Product ---


class ProductCreate(BaseModel):
    division_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    pack_size: str | None = Field(None, max_length=100)
    mrp: float = Field(..., ge=0)
    ptr: float = Field(..., ge=0)
    pts: float = Field(..., ge=0)
    hsn_code: str | None = Field(None, max_length=50)


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    pack_size: str | None = Field(None, max_length=100)
    mrp: float | None = Field(None, ge=0)
    ptr: float | None = Field(None, ge=0)
    pts: float | None = Field(None, ge=0)
    hsn_code: str | None = Field(None, max_length=50)
    is_active: bool | None = None


class ProductOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    division_id: uuid.UUID
    name: str
    pack_size: str | None
    mrp: float
    ptr: float
    pts: float
    hsn_code: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
