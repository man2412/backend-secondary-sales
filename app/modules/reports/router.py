from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BeforeValidator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.responses import ok
from app.models.user import User
from app.modules.reports.service import ReportsService

router = APIRouter(prefix="/reports", tags=["reports"])


def _coerce_query_date(v: object) -> date:
    """Accept `YYYY-MM-DD` or ISO datetimes (date part only) for query params."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        if "T" in s:
            return date.fromisoformat(s.split("T", 1)[0])
        if " " in s and len(s) >= 10:
            return date.fromisoformat(s.split(" ", 1)[0])
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return date.fromisoformat(s[:10])
        return date.fromisoformat(s)
    raise TypeError(f"Expected date or str, got {type(v)}")


_ReportDate = Annotated[date, BeforeValidator(_coerce_query_date)]


@router.get("/secondary-sales/analytics")
async def report_secondary_sales_analytics(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    date_from: Annotated[_ReportDate, Query(description="Calendar date, e.g. 2026-03-24 (ISO datetime OK; time is ignored)")],
    date_to: Annotated[_ReportDate, Query(description="Calendar date, e.g. 2026-03-24 (ISO datetime OK; time is ignored)")],
    company_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
    mr_id: Annotated[UUID | None, Query()] = None,
    doctor_id: Annotated[UUID | None, Query()] = None,
    headquarter_id: Annotated[UUID | None, Query()] = None,
    location_id: Annotated[UUID | None, Query()] = None,
    product_id: Annotated[UUID | None, Query()] = None,
    division_id: Annotated[UUID | None, Query()] = None,
    state_id: Annotated[UUID | None, Query()] = None,
    include_summary: Annotated[bool, Query()] = True,
    timeseries_bucket: Annotated[str | None, Query(description="day | week | month; omit to skip series")] = None,
    pie: Annotated[
        str | None,
        Query(
            description="Comma-separated: product, location, headquarter (or hq), division, rsm, asm",
        ),
    ] = None,
) -> dict:
    try:
        out = await ReportsService().secondary_sales_analytics(
            db,
            current,
            date_from=date_from,
            date_to=date_to,
            company_id_query=company_id,
            include_inactive=include_inactive,
            mr_id_filter=mr_id,
            doctor_id=doctor_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            product_id=product_id,
            division_id=division_id,
            state_id=state_id,
            include_summary=include_summary,
            timeseries_bucket=timeseries_bucket,
            pie_param=pie,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=out.model_dump(mode="json"))
