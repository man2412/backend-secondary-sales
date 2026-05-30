"""HTTP layer for the role-scoped sales dashboard."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BeforeValidator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.responses import ok
from app.models.user import User
from app.modules.dashboard.repository import DashboardFilters
from app.modules.dashboard.service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Query param helpers
# ---------------------------------------------------------------------------


def _coerce_query_date(v: object) -> date:
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


_QDate = Annotated[date, BeforeValidator(_coerce_query_date)]


def _build_filters(
    *,
    state_id: UUID | None,
    headquarter_id: UUID | None,
    division_id: UUID | None,
    product_id: UUID | None,
    doctor_id: UUID | None,
    medical_store_id: UUID | None,
    include_inactive: bool,
) -> DashboardFilters:
    return DashboardFilters(
        state_id=state_id,
        headquarter_id=headquarter_id,
        division_id=division_id,
        product_id=product_id,
        doctor_id=doctor_id,
        medical_store_id=medical_store_id,
        active_only=not include_inactive,
    )


def _map_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/overview")
async def dashboard_overview(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    scope_user_id: Annotated[UUID | None, Query(description="Drill into a subordinate; defaults to caller")] = None,
    state_id: Annotated[UUID | None, Query()] = None,
    headquarter_id: Annotated[UUID | None, Query()] = None,
    division_id: Annotated[UUID | None, Query()] = None,
    product_id: Annotated[UUID | None, Query()] = None,
    doctor_id: Annotated[UUID | None, Query()] = None,
    medical_store_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    """KPI cards for the FSO/ASM/RSM dashboard.

    Returns yearly, quarterly and monthly totals (with previous-period delta %)
    for the caller's reporting subtree, or for a specific subordinate when
    `scope_user_id` is provided.
    """
    try:
        out = await DashboardService().overview(
            db,
            current,
            scope_user_id=scope_user_id,
            filters=_build_filters(
                state_id=state_id,
                headquarter_id=headquarter_id,
                division_id=division_id,
                product_id=product_id,
                doctor_id=doctor_id,
                medical_store_id=medical_store_id,
                include_inactive=include_inactive,
            ),
        )
    except (PermissionError, ValueError) as e:
        raise _map_errors(e) from e
    return ok(data=out.model_dump(mode="json"))


@router.get("/sales-trend")
async def dashboard_sales_trend(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    date_from: Annotated[_QDate, Query()],
    date_to: Annotated[_QDate, Query()],
    bucket: Annotated[str, Query(description="month | quarter | year")] = "month",
    scope_user_id: Annotated[UUID | None, Query()] = None,
    state_id: Annotated[UUID | None, Query()] = None,
    headquarter_id: Annotated[UUID | None, Query()] = None,
    division_id: Annotated[UUID | None, Query()] = None,
    product_id: Annotated[UUID | None, Query()] = None,
    doctor_id: Annotated[UUID | None, Query()] = None,
    medical_store_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    """Sales revenue performance bar chart data."""
    try:
        out = await DashboardService().trend(
            db,
            current,
            scope_user_id=scope_user_id,
            bucket=bucket,
            date_from=date_from,
            date_to=date_to,
            filters=_build_filters(
                state_id=state_id,
                headquarter_id=headquarter_id,
                division_id=division_id,
                product_id=product_id,
                doctor_id=doctor_id,
                medical_store_id=medical_store_id,
                include_inactive=include_inactive,
            ),
        )
    except (PermissionError, ValueError) as e:
        raise _map_errors(e) from e
    return ok(data=out.model_dump(mode="json"))


@router.get("/top-products")
async def dashboard_top_products(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    date_from: Annotated[_QDate, Query()],
    date_to: Annotated[_QDate, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    scope_user_id: Annotated[UUID | None, Query()] = None,
    state_id: Annotated[UUID | None, Query()] = None,
    headquarter_id: Annotated[UUID | None, Query()] = None,
    division_id: Annotated[UUID | None, Query()] = None,
    doctor_id: Annotated[UUID | None, Query()] = None,
    medical_store_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    try:
        out = await DashboardService().top_list(
            db,
            current,
            dimension="product",
            scope_user_id=scope_user_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            filters=_build_filters(
                state_id=state_id,
                headquarter_id=headquarter_id,
                division_id=division_id,
                product_id=None,
                doctor_id=doctor_id,
                medical_store_id=medical_store_id,
                include_inactive=include_inactive,
            ),
        )
    except (PermissionError, ValueError) as e:
        raise _map_errors(e) from e
    return ok(data=out.model_dump(mode="json"))


@router.get("/top-doctors")
async def dashboard_top_doctors(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    date_from: Annotated[_QDate, Query()],
    date_to: Annotated[_QDate, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    scope_user_id: Annotated[UUID | None, Query()] = None,
    state_id: Annotated[UUID | None, Query()] = None,
    headquarter_id: Annotated[UUID | None, Query()] = None,
    division_id: Annotated[UUID | None, Query()] = None,
    product_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    try:
        out = await DashboardService().top_list(
            db,
            current,
            dimension="doctor",
            scope_user_id=scope_user_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            filters=_build_filters(
                state_id=state_id,
                headquarter_id=headquarter_id,
                division_id=division_id,
                product_id=product_id,
                doctor_id=None,
                medical_store_id=None,
                include_inactive=include_inactive,
            ),
        )
    except (PermissionError, ValueError) as e:
        raise _map_errors(e) from e
    return ok(data=out.model_dump(mode="json"))


@router.get("/top-medical-stores")
async def dashboard_top_medical_stores(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    date_from: Annotated[_QDate, Query()],
    date_to: Annotated[_QDate, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    scope_user_id: Annotated[UUID | None, Query()] = None,
    state_id: Annotated[UUID | None, Query()] = None,
    headquarter_id: Annotated[UUID | None, Query()] = None,
    division_id: Annotated[UUID | None, Query()] = None,
    product_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    try:
        out = await DashboardService().top_list(
            db,
            current,
            dimension="medical_store",
            scope_user_id=scope_user_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            filters=_build_filters(
                state_id=state_id,
                headquarter_id=headquarter_id,
                division_id=division_id,
                product_id=product_id,
                doctor_id=None,
                medical_store_id=None,
                include_inactive=include_inactive,
            ),
        )
    except (PermissionError, ValueError) as e:
        raise _map_errors(e) from e
    return ok(data=out.model_dump(mode="json"))


@router.get("/product-growth")
async def dashboard_product_growth(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    mode: Annotated[str, Query(description="mom | qoq | yoy")] = "mom",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    scope_user_id: Annotated[UUID | None, Query()] = None,
    state_id: Annotated[UUID | None, Query()] = None,
    headquarter_id: Annotated[UUID | None, Query()] = None,
    division_id: Annotated[UUID | None, Query()] = None,
    doctor_id: Annotated[UUID | None, Query()] = None,
    medical_store_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    """Top growing and degrowing products comparing the current period vs the
    previous comparable period (MoM, QoQ, YoY)."""
    try:
        out = await DashboardService().product_growth(
            db,
            current,
            scope_user_id=scope_user_id,
            mode=mode,
            limit=limit,
            filters=_build_filters(
                state_id=state_id,
                headquarter_id=headquarter_id,
                division_id=division_id,
                product_id=None,
                doctor_id=doctor_id,
                medical_store_id=medical_store_id,
                include_inactive=include_inactive,
            ),
        )
    except (PermissionError, ValueError) as e:
        raise _map_errors(e) from e
    return ok(data=out.model_dump(mode="json"))


@router.get("/team")
async def dashboard_team(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    scope_user_id: Annotated[UUID | None, Query()] = None,
) -> dict:
    """Hierarchical drill-down: returns the path from the caller down to
    `scope_user_id` (or the caller themselves) and the direct reports of that
    node so the UI can render cascading filters."""
    try:
        out = await DashboardService().team(db, current, scope_user_id=scope_user_id)
    except (PermissionError, ValueError) as e:
        raise _map_errors(e) from e
    return ok(data=out.model_dump(mode="json"))


@router.get("/filters")
async def dashboard_filter_sources(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    date_from: Annotated[_QDate, Query()],
    date_to: Annotated[_QDate, Query()],
    scope_user_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    """Returns the set of states, HQs, divisions, products, doctors and stores
    that the scope user has actually transacted with in the window — perfect
    for populating filter drop-downs."""
    try:
        out = await DashboardService().filter_sources(
            db,
            current,
            scope_user_id=scope_user_id,
            date_from=date_from,
            date_to=date_to,
            active_only=not include_inactive,
        )
    except (PermissionError, ValueError) as e:
        raise _map_errors(e) from e
    return ok(data=out.model_dump(mode="json"))


@router.get("/export")
async def dashboard_export(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    date_from: Annotated[_QDate, Query()],
    date_to: Annotated[_QDate, Query()],
    scope_user_id: Annotated[UUID | None, Query()] = None,
    state_id: Annotated[UUID | None, Query()] = None,
    headquarter_id: Annotated[UUID | None, Query()] = None,
    division_id: Annotated[UUID | None, Query()] = None,
    product_id: Annotated[UUID | None, Query()] = None,
    doctor_id: Annotated[UUID | None, Query()] = None,
    medical_store_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> Response:
    """CSV export covering top products, doctors and medical stores for the
    current scope."""
    try:
        body = await DashboardService().export_csv(
            db,
            current,
            scope_user_id=scope_user_id,
            date_from=date_from,
            date_to=date_to,
            filters=_build_filters(
                state_id=state_id,
                headquarter_id=headquarter_id,
                division_id=division_id,
                product_id=product_id,
                doctor_id=doctor_id,
                medical_store_id=medical_store_id,
                include_inactive=include_inactive,
            ),
        )
    except (PermissionError, ValueError) as e:
        raise _map_errors(e) from e

    filename = f"aptus-dashboard-{date_from.isoformat()}-{date_to.isoformat()}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
