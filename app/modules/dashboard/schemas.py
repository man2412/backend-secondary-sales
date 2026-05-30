"""Pydantic schemas for the role-scoped sales dashboard."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import UserRole


GrowthMode = Literal["mom", "qoq", "yoy"]
GrowthDirection = Literal["growing", "degrowing"]
TrendBucket = Literal["month", "quarter", "year"]


# ---------------------------------------------------------------------------
# Header / overview
# ---------------------------------------------------------------------------


class PeriodTotals(BaseModel):
    """Sale totals for a contiguous date window."""

    label: str
    date_from: date
    date_to: date
    revenue: float
    sale_qty: int
    line_count: int
    delta_pct: float | None = Field(
        default=None,
        description="% change vs the previous comparable window (None when prior had no sales)",
    )


class DashboardOverview(BaseModel):
    scope_user_id: uuid.UUID
    scope_user_name: str
    scope_user_role: UserRole
    yearly: PeriodTotals
    quarterly: PeriodTotals
    monthly: PeriodTotals


# ---------------------------------------------------------------------------
# Trend (bar chart)
# ---------------------------------------------------------------------------


class TrendPoint(BaseModel):
    period_key: str
    label: str
    revenue: float
    sale_qty: int


class TrendOut(BaseModel):
    bucket: TrendBucket
    date_from: date
    date_to: date
    points: list[TrendPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top N (products / doctors / medical stores)
# ---------------------------------------------------------------------------


class TopEntity(BaseModel):
    id: uuid.UUID
    label: str
    revenue: float
    sale_qty: int
    pct_revenue: float = Field(description="Share of selection revenue, 0-100")


class TopListOut(BaseModel):
    dimension: Literal["product", "doctor", "medical_store", "mr"]
    date_from: date
    date_to: date
    items: list[TopEntity] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Growth (MoM / QoQ / YoY top growing & degrowing products)
# ---------------------------------------------------------------------------


class ProductGrowthRow(BaseModel):
    product_id: uuid.UUID
    product_name: str
    current_revenue: float
    previous_revenue: float
    delta_abs: float
    delta_pct: float | None = Field(
        default=None,
        description="None when previous revenue was 0 (infinite growth)",
    )


class ProductGrowthOut(BaseModel):
    mode: GrowthMode
    current_from: date
    current_to: date
    previous_from: date
    previous_to: date
    growing: list[ProductGrowthRow] = Field(default_factory=list)
    degrowing: list[ProductGrowthRow] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Team / hierarchy navigation
# ---------------------------------------------------------------------------


class TeamNode(BaseModel):
    id: uuid.UUID
    full_name: str
    role: UserRole
    employee_code: str | None
    direct_report_count: int
    mr_descendant_count: int


class TeamChainEntry(BaseModel):
    id: uuid.UUID
    full_name: str
    role: UserRole


class TeamOut(BaseModel):
    scope_user_id: uuid.UUID
    scope_user_name: str
    scope_user_role: UserRole
    chain: list[TeamChainEntry] = Field(
        default_factory=list,
        description="Path from logged-in user down to the selected scope (inclusive).",
    )
    direct_reports: list[TeamNode] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Filter sources (drop-downs)
# ---------------------------------------------------------------------------


class FilterOption(BaseModel):
    id: uuid.UUID
    label: str
    sublabel: str | None = None


class DashboardFiltersOut(BaseModel):
    states: list[FilterOption] = Field(default_factory=list)
    headquarters: list[FilterOption] = Field(default_factory=list)
    divisions: list[FilterOption] = Field(default_factory=list)
    products: list[FilterOption] = Field(default_factory=list)
    doctors: list[FilterOption] = Field(default_factory=list)
    medical_stores: list[FilterOption] = Field(default_factory=list)
