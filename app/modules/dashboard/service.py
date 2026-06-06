"""Dashboard service: enforces RBAC scope, builds period windows, runs aggregates."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.sale import SecondarySale
from app.models.user import User
from app.modules.dashboard.repository import DashboardFilters, DashboardRepository
from app.modules.dashboard.schemas import (
    DashboardFiltersOut,
    DashboardOverview,
    FilterOption,
    PeriodTotals,
    ProductGrowthOut,
    ProductGrowthRow,
    TeamChainEntry,
    TeamNode,
    TeamOut,
    TopEntity,
    TopListOut,
    TrendOut,
    TrendPoint,
)
from app.modules.users.repository import UserRepository
from app.modules.users.service import UserService


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------


def _next_month_first(d: date) -> date:
    y = d.year + (1 if d.month == 12 else 0)
    m = 1 if d.month == 12 else d.month + 1
    return date(y, m, 1)


def _days_in_month(year: int, month: int) -> int:
    first = date(year, month, 1)
    return (_next_month_first(first) - first).days


def _add_months(d: date, months: int) -> date:
    """Calendar-correct month arithmetic, snapping to the last day when needed."""
    total = d.year * 12 + (d.month - 1) + months
    y, m = divmod(total, 12)
    m += 1
    last_day = _days_in_month(y, m)
    return date(y, m, min(d.day, last_day))


def _month_window(any_day: date) -> tuple[date, date]:
    start = any_day.replace(day=1)
    end = _next_month_first(start) - timedelta(days=1)
    return start, end


def _quarter_window(any_day: date) -> tuple[date, date]:
    q_start_month = ((any_day.month - 1) // 3) * 3 + 1
    start = date(any_day.year, q_start_month, 1)
    end_first = _add_months(start, 3)
    return start, end_first - timedelta(days=1)


def _year_window(any_day: date) -> tuple[date, date]:
    return date(any_day.year, 1, 1), date(any_day.year, 12, 31)


def _previous_window(start: date, end: date, mode: str) -> tuple[date, date]:
    if mode == "mom":
        prev_any = (start - timedelta(days=1))
        return _month_window(prev_any)
    if mode == "qoq":
        prev_any = (start - timedelta(days=1))
        return _quarter_window(prev_any)
    if mode == "yoy":
        return (
            date(start.year - 1, start.month, min(start.day, _days_in_month(start.year - 1, start.month))),
            date(end.year - 1, end.month, min(end.day, _days_in_month(end.year - 1, end.month))),
        )
    raise ValueError(f"Unsupported growth mode: {mode}")


def _label_and_delta_mode(date_from: date, date_to: date) -> tuple[str, str]:
    """Infer display label and prior-period comparison mode from a date window."""
    m_start, m_end = _month_window(date_from)
    if date_from == m_start and date_to == m_end:
        return date_from.strftime("%B %Y"), "mom"

    q_start, q_end = _quarter_window(date_from)
    if date_from == q_start and date_to == q_end:
        q = (date_from.month - 1) // 3 + 1
        return f"Q{q} {date_from.year}", "qoq"

    y_start, y_end = _year_window(date_from)
    if date_from == y_start and date_to == y_end:
        return str(date_from.year), "yoy"

    return f"{date_from.isoformat()} – {date_to.isoformat()}", "yoy"


def _label_for_trend(period_key_iso: str, bucket: str) -> str:
    """Turn the ISO string from `date_trunc` (e.g. '2026-04-01 00:00:00') into a label."""
    try:
        # date_trunc → 'YYYY-MM-DD HH:MM:SS' (or with TZ); take the date part.
        d = date.fromisoformat(period_key_iso[:10])
    except ValueError:
        return period_key_iso
    if bucket == "month":
        return d.strftime("%b %Y")
    if bucket == "quarter":
        q = (d.month - 1) // 3 + 1
        return f"Q{q} {d.year}"
    if bucket == "year":
        return str(d.year)
    return d.isoformat()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class DashboardService:
    def __init__(
        self,
        repo: DashboardRepository | None = None,
        user_repo: UserRepository | None = None,
    ) -> None:
        self._repo = repo or DashboardRepository()
        self._users = user_repo or UserRepository()

    # ------------------------------------------------------------------
    # Scope resolution
    # ------------------------------------------------------------------

    async def _resolve_scope(
        self,
        db: AsyncSession,
        caller: User,
        scope_user_id: uuid.UUID | None,
    ) -> User:
        """The user whose subtree to aggregate over. Defaults to the caller.

        `scope_user_id` MUST be inside the caller's subtree (or be the caller).
        """
        if scope_user_id is None or scope_user_id == caller.id:
            return caller

        if caller.role in (UserRole.SUPER_ADMIN, UserRole.SALES_DIRECTOR):
            target = await self._users.get_by_id(db, scope_user_id)
            if target is None:
                raise ValueError("Scope user not found")
            return target

        allowed = await self._users.list_user_ids_in_subtree(db, caller.id)
        if scope_user_id not in allowed:
            raise PermissionError("Cannot drill into this user (out of your team)")
        target = await self._users.get_by_id(db, scope_user_id)
        if target is None:
            raise ValueError("Scope user not found")
        return target

    async def _mr_ids_for_scope(self, db: AsyncSession, scope: User) -> list[uuid.UUID]:
        return list(await UserService().get_visible_mr_ids(db, scope))

    # ------------------------------------------------------------------
    # Overview KPIs (yearly / quarterly / monthly with previous-period delta)
    # ------------------------------------------------------------------

    async def overview(
        self,
        db: AsyncSession,
        caller: User,
        *,
        scope_user_id: uuid.UUID | None,
        date_from: date | None = None,
        date_to: date | None = None,
        today: date | None = None,
        filters: DashboardFilters | None = None,
    ) -> DashboardOverview:
        if (date_from is None) ^ (date_to is None):
            raise ValueError("date_from and date_to must both be provided or both omitted")
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValueError("date_from must be on or before date_to")

        scope = await self._resolve_scope(db, caller, scope_user_id)
        mr_ids = await self._mr_ids_for_scope(db, scope)
        flt = filters or DashboardFilters()
        ref = date_to or today or date.today()

        yearly = await self._period_totals(db, mr_ids, *_year_window(ref), "yoy", flt, "Yearly")
        quarterly = await self._period_totals(
            db, mr_ids, *_quarter_window(ref), "qoq", flt, "Quarterly"
        )
        monthly = await self._period_totals(
            db, mr_ids, *_month_window(ref), "mom", flt, "Monthly"
        )

        selected: PeriodTotals | None = None
        if date_from is not None and date_to is not None:
            label, delta_mode = _label_and_delta_mode(date_from, date_to)
            selected = await self._period_totals(
                db, mr_ids, date_from, date_to, delta_mode, flt, label
            )

        return DashboardOverview(
            scope_user_id=scope.id,
            scope_user_name=scope.full_name,
            scope_user_role=scope.role,
            selected=selected,
            yearly=yearly,
            quarterly=quarterly,
            monthly=monthly,
        )

    async def _period_totals(
        self,
        db: AsyncSession,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        delta_mode: str,
        filters: DashboardFilters,
        label: str,
    ) -> PeriodTotals:
        rev, qty, lc = await self._repo.totals(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to, filters=filters
        )
        pf, pt = _previous_window(date_from, date_to, delta_mode)
        prev_rev, _, _ = await self._repo.totals(
            db, mr_ids=mr_ids, date_from=pf, date_to=pt, filters=filters
        )
        delta_pct: float | None
        if prev_rev > 0:
            delta_pct = round((rev - prev_rev) / prev_rev * 100.0, 2)
        else:
            delta_pct = None
        return PeriodTotals(
            label=label,
            date_from=date_from,
            date_to=date_to,
            revenue=rev,
            sale_qty=qty,
            line_count=lc,
            delta_pct=delta_pct,
        )

    # ------------------------------------------------------------------
    # Trend (bar chart)
    # ------------------------------------------------------------------

    async def trend(
        self,
        db: AsyncSession,
        caller: User,
        *,
        scope_user_id: uuid.UUID | None,
        bucket: str,
        date_from: date,
        date_to: date,
        filters: DashboardFilters | None = None,
    ) -> TrendOut:
        if bucket not in ("month", "quarter", "year"):
            raise ValueError("bucket must be month, quarter or year")
        if date_from > date_to:
            raise ValueError("date_from must be on or before date_to")
        scope = await self._resolve_scope(db, caller, scope_user_id)
        mr_ids = await self._mr_ids_for_scope(db, scope)
        flt = filters or DashboardFilters()
        raw = await self._repo.trend(
            db,
            mr_ids=mr_ids,
            date_from=date_from,
            date_to=date_to,
            bucket=bucket,
            filters=flt,
        )
        points = [
            TrendPoint(
                period_key=pk,
                label=_label_for_trend(pk, bucket),
                revenue=rev,
                sale_qty=qty,
            )
            for pk, rev, qty in raw
        ]
        return TrendOut(bucket=bucket, date_from=date_from, date_to=date_to, points=points)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Top N
    # ------------------------------------------------------------------

    async def top_list(
        self,
        db: AsyncSession,
        caller: User,
        *,
        dimension: str,
        scope_user_id: uuid.UUID | None,
        date_from: date,
        date_to: date,
        limit: int,
        filters: DashboardFilters | None = None,
    ) -> TopListOut:
        if dimension not in ("product", "doctor", "medical_store"):
            raise ValueError("dimension must be product, doctor, or medical_store")
        if date_from > date_to:
            raise ValueError("date_from must be on or before date_to")
        scope = await self._resolve_scope(db, caller, scope_user_id)
        mr_ids = await self._mr_ids_for_scope(db, scope)
        flt = filters or DashboardFilters()
        if dimension == "product":
            raw = await self._repo.top_products(
                db, mr_ids=mr_ids, date_from=date_from, date_to=date_to, filters=flt, limit=limit
            )
        elif dimension == "doctor":
            raw = await self._repo.top_doctors(
                db, mr_ids=mr_ids, date_from=date_from, date_to=date_to, filters=flt, limit=limit
            )
        else:
            raw = await self._repo.top_medical_stores(
                db, mr_ids=mr_ids, date_from=date_from, date_to=date_to, filters=flt, limit=limit
            )
        total_rev = sum(r[2] for r in raw) or 0.0
        items = [
            TopEntity(
                id=eid,
                label=label,
                revenue=rev,
                sale_qty=qty,
                pct_revenue=round((rev / total_rev * 100.0) if total_rev > 0 else 0.0, 4),
            )
            for eid, label, rev, qty in raw
        ]
        return TopListOut(
            dimension=dimension,  # type: ignore[arg-type]
            date_from=date_from,
            date_to=date_to,
            items=items,
        )

    # ------------------------------------------------------------------
    # Growth (MoM / QoQ / YoY)
    # ------------------------------------------------------------------

    async def product_growth(
        self,
        db: AsyncSession,
        caller: User,
        *,
        scope_user_id: uuid.UUID | None,
        mode: str,
        today: date | None = None,
        limit: int = 10,
        filters: DashboardFilters | None = None,
    ) -> ProductGrowthOut:
        if mode not in ("mom", "qoq", "yoy"):
            raise ValueError("mode must be mom, qoq or yoy")
        scope = await self._resolve_scope(db, caller, scope_user_id)
        mr_ids = await self._mr_ids_for_scope(db, scope)
        flt = filters or DashboardFilters()
        ref = today or date.today()

        if mode == "mom":
            cur_from, cur_to = _month_window(ref)
        elif mode == "qoq":
            cur_from, cur_to = _quarter_window(ref)
        else:
            cur_from, cur_to = _year_window(ref)
        prev_from, prev_to = _previous_window(cur_from, cur_to, mode)

        cur_map = await self._repo.product_revenue_map(
            db,
            mr_ids=mr_ids,
            date_from=cur_from,
            date_to=cur_to,
            filters=flt,
        )
        prev_map = await self._repo.product_revenue_map(
            db,
            mr_ids=mr_ids,
            date_from=prev_from,
            date_to=prev_to,
            filters=flt,
        )

        all_ids = set(cur_map.keys()) | set(prev_map.keys())
        rows: list[ProductGrowthRow] = []
        for pid in all_ids:
            cur_name, cur_rev = cur_map.get(pid, ("", 0.0))
            prev_name, prev_rev = prev_map.get(pid, ("", 0.0))
            name = cur_name or prev_name or "Unknown product"
            delta_abs = cur_rev - prev_rev
            if prev_rev > 0:
                delta_pct = round(delta_abs / prev_rev * 100.0, 2)
            else:
                delta_pct = None
            rows.append(
                ProductGrowthRow(
                    product_id=pid,
                    product_name=name,
                    current_revenue=cur_rev,
                    previous_revenue=prev_rev,
                    delta_abs=delta_abs,
                    delta_pct=delta_pct,
                )
            )

        def _grow_key(r: ProductGrowthRow) -> tuple[int, float]:
            # New products (prev=0, cur>0) rank first inside growers via Inf — sort group key.
            if r.previous_revenue == 0 and r.current_revenue > 0:
                return (1, r.current_revenue)
            return (0, r.delta_pct or 0.0)

        growers = [r for r in rows if r.delta_abs > 0]
        growers.sort(key=_grow_key, reverse=True)

        degrowers = [r for r in rows if r.delta_abs < 0]
        degrowers.sort(key=lambda r: (r.delta_pct if r.delta_pct is not None else -100.0))

        return ProductGrowthOut(
            mode=mode,  # type: ignore[arg-type]
            current_from=cur_from,
            current_to=cur_to,
            previous_from=prev_from,
            previous_to=prev_to,
            growing=growers[:limit],
            degrowing=degrowers[:limit],
        )

    # ------------------------------------------------------------------
    # Team hierarchy (drill-down navigator)
    # ------------------------------------------------------------------

    async def team(
        self,
        db: AsyncSession,
        caller: User,
        *,
        scope_user_id: uuid.UUID | None,
    ) -> TeamOut:
        scope = await self._resolve_scope(db, caller, scope_user_id)

        # Direct reports of the scope user
        q_children = text(
            """
            SELECT u.id, u.full_name, u.role, u.employee_code,
                   (SELECT COUNT(1) FROM users c WHERE c.reports_to = u.id AND c.is_active) AS direct_count
            FROM users u
            WHERE u.reports_to = :uid AND u.is_active = true
            ORDER BY u.full_name
            """
        )
        rows = (await db.execute(q_children, {"uid": str(scope.id)})).fetchall()

        children: list[TeamNode] = []
        for r in rows:
            child_id = uuid.UUID(str(r[0]))
            mr_count = len(await self._users.list_mr_ids_under_manager(db, child_id))
            role_val = r[2]
            role_str = role_val.value if hasattr(role_val, "value") else str(role_val)
            children.append(
                TeamNode(
                    id=child_id,
                    full_name=str(r[1]),
                    role=UserRole(role_str),
                    employee_code=r[3],
                    direct_report_count=int(r[4] or 0),
                    mr_descendant_count=mr_count,
                )
            )

        # Chain from caller -> scope (inclusive)
        chain: list[TeamChainEntry] = []
        if scope.id == caller.id:
            chain.append(TeamChainEntry(id=caller.id, full_name=caller.full_name, role=caller.role))
        else:
            chain_rows = await self._users.reporting_chain(db, scope.id)
            # reporting_chain returns scope -> ... -> top. Reverse so caller end is first.
            chain_reversed = list(reversed(chain_rows))
            include = False
            for c in chain_reversed:
                if str(c["id"]) == str(caller.id):
                    include = True
                if include:
                    chain.append(
                        TeamChainEntry(
                            id=uuid.UUID(c["id"]),
                            full_name=c["full_name"],
                            role=UserRole(c["role"]),
                        )
                    )
            if not chain:
                # caller was outside the chain (super admin); still show full chain.
                chain = [
                    TeamChainEntry(
                        id=uuid.UUID(c["id"]),
                        full_name=c["full_name"],
                        role=UserRole(c["role"]),
                    )
                    for c in chain_reversed
                ]

        return TeamOut(
            scope_user_id=scope.id,
            scope_user_name=scope.full_name,
            scope_user_role=scope.role,
            chain=chain,
            direct_reports=children,
        )

    # ------------------------------------------------------------------
    # Filter sources (drop-down lists scoped to the user's recent data)
    # ------------------------------------------------------------------

    async def filter_sources(
        self,
        db: AsyncSession,
        caller: User,
        *,
        scope_user_id: uuid.UUID | None,
        date_from: date,
        date_to: date,
        active_only: bool = True,
    ) -> DashboardFiltersOut:
        scope = await self._resolve_scope(db, caller, scope_user_id)
        mr_ids = await self._mr_ids_for_scope(db, scope)

        async def _opt(rows: list[tuple[uuid.UUID, str, str | None]]) -> list[FilterOption]:
            return [FilterOption(id=r[0], label=r[1], sublabel=r[2]) for r in rows]

        states_ids = await self._repo.distinct_dim_ids(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to,
            column=SecondarySale.state_id, active_only=active_only,
        )
        hq_ids = await self._repo.distinct_dim_ids(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to,
            column=SecondarySale.headquarter_id, active_only=active_only,
        )
        div_ids = await self._repo.distinct_dim_ids(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to,
            column=SecondarySale.division_id, active_only=active_only,
        )
        prod_ids = await self._repo.distinct_dim_ids(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to,
            column=SecondarySale.product_id, active_only=active_only,
        )
        doc_ids = await self._repo.distinct_dim_ids(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to,
            column=SecondarySale.doctor_id, active_only=active_only,
        )
        store_ids = await self._repo.distinct_dim_ids(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to,
            column=SecondarySale.medical_store_id, active_only=active_only,
        )

        return DashboardFiltersOut(
            states=await _opt(await self._repo.names_for_states(db, states_ids)),
            headquarters=await _opt(await self._repo.names_for_headquarters(db, hq_ids)),
            divisions=await _opt(await self._repo.names_for_divisions(db, div_ids)),
            products=await _opt(await self._repo.names_for_products(db, prod_ids)),
            doctors=await _opt(await self._repo.names_for_doctors(db, doc_ids)),
            medical_stores=await _opt(await self._repo.names_for_medical_stores(db, store_ids)),
        )

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    async def export_csv(
        self,
        db: AsyncSession,
        caller: User,
        *,
        scope_user_id: uuid.UUID | None,
        date_from: date,
        date_to: date,
        filters: DashboardFilters | None = None,
    ) -> str:
        scope = await self._resolve_scope(db, caller, scope_user_id)
        mr_ids = await self._mr_ids_for_scope(db, scope)
        flt = filters or DashboardFilters()

        # Reuse top-N (large limit) across dimensions to produce a compact CSV
        top_products = await self._repo.top_products(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to, filters=flt, limit=500
        )
        top_doctors = await self._repo.top_doctors(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to, filters=flt, limit=500
        )
        top_stores = await self._repo.top_medical_stores(
            db, mr_ids=mr_ids, date_from=date_from, date_to=date_to, filters=flt, limit=500
        )

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["# Aptus Sales Dashboard Export"])
        w.writerow(["# Scope", scope.full_name, scope.role.value])
        w.writerow(["# From", date_from.isoformat(), "To", date_to.isoformat()])
        w.writerow([])

        w.writerow(["Section: Top Products"])
        w.writerow(["Rank", "Product", "Revenue", "Sale Qty"])
        for i, (pid, name, rev, qty) in enumerate(top_products, 1):
            w.writerow([i, name, f"{rev:.2f}", qty])
        w.writerow([])

        w.writerow(["Section: Top Doctors"])
        w.writerow(["Rank", "Doctor", "Revenue", "Sale Qty"])
        for i, (did, name, rev, qty) in enumerate(top_doctors, 1):
            w.writerow([i, name, f"{rev:.2f}", qty])
        w.writerow([])

        w.writerow(["Section: Top Medical Stores"])
        w.writerow(["Rank", "Store", "Revenue", "Sale Qty"])
        for i, (sid, name, rev, qty) in enumerate(top_stores, 1):
            w.writerow([i, name, f"{rev:.2f}", qty])

        return buf.getvalue()


# Re-export for convenience
__all__ = ["DashboardService", "DashboardFilters"]
