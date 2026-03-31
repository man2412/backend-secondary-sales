import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import UserRole


class UserOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    supabase_id: uuid.UUID
    company_id: uuid.UUID
    division_id: uuid.UUID | None
    employee_code: str | None
    full_name: str
    email: str
    phone: str | None
    role: UserRole
    reports_to: uuid.UUID | None
    state_id: uuid.UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    company_id: uuid.UUID
    division_id: uuid.UUID | None = None
    employee_code: str | None = None
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    phone: str | None = None
    role: UserRole
    reports_to: uuid.UUID | None = None
    state_id: uuid.UUID | None = None
    supabase_id: uuid.UUID | None = None


class UserUpdate(BaseModel):
    division_id: uuid.UUID | None = None
    employee_code: str | None = None
    full_name: str | None = Field(None, min_length=1, max_length=255)
    email: EmailStr | None = None
    phone: str | None = None
    role: UserRole | None = None
    reports_to: uuid.UUID | None = None
    state_id: uuid.UUID | None = None
    is_active: bool | None = None
