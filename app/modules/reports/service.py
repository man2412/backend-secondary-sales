import uuid
from datetime import date
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.schemas import (
    AnalyticsFiltersApplied,
    AnalyticsSummaryBlock,
    PieDimension,
    PieSeriesOut,
    PieSliceOut,
    SecondarySalesAnalyticsOut,
    TimeSeriesPointOut,
)
from app.modules.users.service import UserService

MAX_REPORT_RANGE_DAYS = 731

_PIE_ALIASES: dict[str, str] = {
    "product": "product",
    "location": "location",
    "headquarter": "headquarter",
    "hq": "headquarter",
    "division": "division",
    "rsm": "rsm",
    "asm": "asm",
}


class ReportsService:
    def __init__(self, repo: ReportsRepository | None = None) -> None:
        self._repo = repo or ReportsRepository()

    def _validate_range(self, date_from: date, date_to: date) -> None:
        if date_from > date_to:
            raise ValueError("date_from must be on or before date_to")
        span = (date_to - date_from).days + 1
        if span > MAX_REPORT_RANGE_DAYS:
            raise ValueError(f"Date range cannot exceed {MAX_REPORT_RANGE_DAYS} days")

    async def _scope_company_id(
        self, db: AsyncSession, user: User, company_id_query: uuid.UUID | None
    ) -> uuid.UUID:
        if user.role == UserRole.SUPER_ADMIN:
            if company_id_query is None:
                raise ValueError("company_id is required")
            return company_id_query
        if company_id_query is not None and company_id_query != user.company_id:
            raise PermissionError("company_id not allowed for this role")
        return user.company_id

    async def _visible_mr_ids(self, db: AsyncSession, user: User) -> list[uuid.UUID]:
        return await UserService().get_visible_mr_ids(db, user)

    def _resolve_mr_scope(
        self, visible: list[uuid.UUID], mr_id_filter: uuid.UUID | None
    ) -> list[uuid.UUID]:
        if mr_id_filter is None:
            return visible
        if mr_id_filter not in visible:
            raise PermissionError("Cannot report on this MR")
        return [mr_id_filter]

    def _company_for_manager_pie(self, user: User, scoped_company_id: uuid.UUID | None) -> uuid.UUID:
        """RSM/ASM pie rolls up users in one company."""
        if user.role == UserRole.SUPER_ADMIN:
            if scoped_company_id is None:
                raise ValueError("company_id is required for RSM/ASM pie charts when using SUPER_ADMIN")
            return scoped_company_id
        return user.company_id

    @staticmethod
    def _parse_pie_dimensions(pie_param: str | None) -> list[str]:
        if not pie_param or not pie_param.strip():
            return []
        out: list[str] = []
        for raw in pie_param.split(","):
            key = raw.strip().lower()
            if not key:
                continue
            mapped = _PIE_ALIASES.get(key)
            if mapped is None:
                raise ValueError(f"Unknown pie dimension: {raw!r} (use product, location, headquarter, division, rsm, asm)")
            if mapped not in out:
                out.append(mapped)
        return out

    @staticmethod
    def _pie_series(dimension: str, rows: list[tuple[uuid.UUID, str, float, int]]) -> PieSeriesOut:
        total_rev = sum(r[2] for r in rows)
        total_qty = sum(r[3] for r in rows)
        slices: list[PieSliceOut] = []
        for eid, label, rev, qty in rows:
            pr = (rev / total_rev * 100.0) if total_rev > 0 else 0.0
            pq = (qty / total_qty * 100.0) if total_qty > 0 else 0.0
            slices.append(
                PieSliceOut(
                    id=eid,
                    label=label,
                    revenue=rev,
                    sale_qty=qty,
                    pct_revenue=round(pr, 6),
                    pct_quantity=round(pq, 6),
                )
            )
        return PieSeriesOut(dimension=cast(PieDimension, dimension), slices=slices)

    async def secondary_sales_analytics(
        self,
        db: AsyncSession,
        user: User,
        *,
        date_from: date,
        date_to: date,
        company_id_query: uuid.UUID | None,
        include_inactive: bool,
        mr_id_filter: uuid.UUID | None,
        doctor_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        division_id: uuid.UUID | None,
        state_id: uuid.UUID | None,
        include_summary: bool,
        timeseries_bucket: str | None,
        pie_param: str | None,
    ) -> SecondarySalesAnalyticsOut:
        self._validate_range(date_from, date_to)
        company_id = await self._scope_company_id(db, user, company_id_query)
        visible = await self._visible_mr_ids(db, user)
        mr_ids = self._resolve_mr_scope(visible, mr_id_filter)
        active_only = not include_inactive
        pie_dims = self._parse_pie_dimensions(pie_param)

        filters = AnalyticsFiltersApplied(
            company_id=company_id,
            mr_id=mr_id_filter,
            doctor_id=doctor_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            product_id=product_id,
            division_id=division_id,
            state_id=state_id,
            include_inactive=include_inactive,
        )

        kwargs = dict(
            mr_ids=mr_ids,
            date_from=date_from,
            date_to=date_to,
            company_id=company_id,
            active_only=active_only,
            doctor_id=doctor_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            product_id=product_id,
            division_id=division_id,
            state_id=state_id,
        )

        summary_block = None
        if include_summary:
            lc, sq, fq, ta = await self._repo.analytics_summary(db, **kwargs)
            summary_block = AnalyticsSummaryBlock(
                line_count=lc,
                total_sale_qty=sq,
                total_free_qty=fq,
                total_amount=ta,
            )

        ts_out: list[TimeSeriesPointOut] | None = None
        if timeseries_bucket:
            if timeseries_bucket not in ("day", "week", "month"):
                raise ValueError("timeseries_bucket must be day, week, or month")
            raw_ts = await self._repo.analytics_timeseries(db, **kwargs, bucket=timeseries_bucket)
            ts_out = [
                TimeSeriesPointOut(period=p, revenue=rev, sale_qty=sq, free_qty=fq)
                for p, rev, sq, fq in raw_ts
            ]

        pies: list[PieSeriesOut] = []
        for dim in pie_dims:
            if dim in ("product", "location", "headquarter", "division"):
                fn = {
                    "product": self._repo.analytics_pie_by_product,
                    "location": self._repo.analytics_pie_by_location,
                    "headquarter": self._repo.analytics_pie_by_headquarter,
                    "division": self._repo.analytics_pie_by_division,
                }[dim]
                rows = await fn(db, **kwargs)
                pies.append(self._pie_series(dim, rows))
            else:
                cid = self._company_for_manager_pie(user, company_id)
                rows = await self._repo.analytics_pie_by_manager_role(
                    db,
                    **kwargs,
                    company_id=cid,
                    manager_role="RSM" if dim == "rsm" else "ASM",
                )
                pies.append(self._pie_series(dim, rows))

        return SecondarySalesAnalyticsOut(
            date_from=date_from,
            date_to=date_to,
            filters=filters,
            summary=summary_block,
            time_series=ts_out,
            pies=pies,
        )
