import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SuperStockistOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    unique_code: str | None
    gst_number: str | None
    drug_licence: str | None
    pan: str | None
    address: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SuperStockistCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    unique_code: str | None = None
    gst_number: str | None = None
    drug_licence: str | None = None
    pan: str | None = None
    address: str | None = None


class SuperStockistUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    unique_code: str | None = None
    gst_number: str | None = None
    drug_licence: str | None = None
    pan: str | None = None
    address: str | None = None
    is_active: bool | None = None


class StockistOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    super_stockist_id: uuid.UUID | None
    name: str
    unique_code: str | None
    gst_number: str | None
    drug_licence: str | None
    pan: str | None
    address: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class StockistCreate(BaseModel):
    super_stockist_id: uuid.UUID | None = None
    name: str = Field(..., min_length=1, max_length=255)
    unique_code: str | None = None
    gst_number: str | None = None
    drug_licence: str | None = None
    pan: str | None = None
    address: str | None = None


class StockistUpdate(BaseModel):
    super_stockist_id: uuid.UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=255)
    unique_code: str | None = None
    gst_number: str | None = None
    drug_licence: str | None = None
    pan: str | None = None
    address: str | None = None
    is_active: bool | None = None


class MedicalStoreOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    stockist_id: uuid.UUID | None
    name: str
    unique_code: str | None
    gst_number: str | None
    drug_licence: str | None
    pan: str | None
    address: str | None
    location_id: uuid.UUID | None
    alternate_names: list[str] | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class MedicalStoreCreate(BaseModel):
    stockist_id: uuid.UUID | None = None
    name: str = Field(..., min_length=1, max_length=255)
    unique_code: str | None = None
    gst_number: str | None = None
    drug_licence: str | None = None
    pan: str | None = None
    address: str | None = None
    location_id: uuid.UUID | None = None
    alternate_names: list[str] | None = None


class MedicalStoreUpdate(BaseModel):
    stockist_id: uuid.UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=255)
    unique_code: str | None = None
    gst_number: str | None = None
    drug_licence: str | None = None
    pan: str | None = None
    address: str | None = None
    location_id: uuid.UUID | None = None
    alternate_names: list[str] | None = None
    is_active: bool | None = None
