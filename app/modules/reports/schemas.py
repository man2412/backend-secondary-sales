import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

PieDimension = Literal["product", "location", "headquarter", "division", "rsm", "asm"]
TimeseriesBucket = Literal["day", "week", "month"]


class AnalyticsFiltersApplied(BaseModel):
    company_id: uuid.UUID | None = None
    mr_id: uuid.UUID | None = None
    doctor_id: uuid.UUID | None = None
    headquarter_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    division_id: uuid.UUID | None = None
    state_id: uuid.UUID | None = None
    include_inactive: bool = False


class AnalyticsSummaryBlock(BaseModel):
    line_count: int
    total_sale_qty: int
    total_free_qty: int
    total_amount: float


class TimeSeriesPointOut(BaseModel):
    period: str
    revenue: float
    sale_qty: int
    free_qty: int


class PieSliceOut(BaseModel):
    id: uuid.UUID
    label: str
    revenue: float
    sale_qty: int
    pct_revenue: float = Field(description="Share of total revenue in this pie, 0–100")
    pct_quantity: float = Field(description="Share of total sale_qty in this pie, 0–100")


class PieSeriesOut(BaseModel):
    dimension: PieDimension
    slices: list[PieSliceOut]


class SecondarySalesAnalyticsOut(BaseModel):
    date_from: date
    date_to: date
    filters: AnalyticsFiltersApplied
    summary: AnalyticsSummaryBlock | None = None
    time_series: list[TimeSeriesPointOut] | None = None
    pies: list[PieSeriesOut] = Field(default_factory=list)
