"""SQL queries powering the dashboard. All queries are mr_id-scoped so role-based
visibility is enforced by the service layer choosing which MRs to include."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor import Doctor
from app.models.master import Division, Headquarter, Product, State
from app.models.sale import SecondarySale
from app.models.stockist import MedicalStore


class DashboardFilters:
    """Optional dimension filters reused across queries."""

    def __init__(
        self,
        *,
        state_id: uuid.UUID | None = None,
        headquarter_id: uuid.UUID | None = None,
        division_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        doctor_id: uuid.UUID | None = None,
        medical_store_id: uuid.UUID | None = None,
        active_only: bool = True,
    ) -> None:
        self.state_id = state_id
        self.headquarter_id = headquarter_id
        self.division_id = division_id
        self.product_id = product_id
        self.doctor_id = doctor_id
        self.medical_store_id = medical_store_id
        self.active_only = active_only

    def apply(self, mr_ids: Iterable[uuid.UUID], date_from: date, date_to: date):
        ids = list(mr_ids)
        conds = [
            SecondarySale.mr_id.in_(ids) if ids else (SecondarySale.mr_id.in_([])),
            SecondarySale.sale_date >= date_from,
            SecondarySale.sale_date <= date_to,
        ]
        if self.active_only:
            conds.append(SecondarySale.is_active.is_(True))
        if self.state_id is not None:
            conds.append(SecondarySale.state_id == self.state_id)
        if self.headquarter_id is not None:
            conds.append(SecondarySale.headquarter_id == self.headquarter_id)
        if self.division_id is not None:
            conds.append(SecondarySale.division_id == self.division_id)
        if self.product_id is not None:
            conds.append(SecondarySale.product_id == self.product_id)
        if self.doctor_id is not None:
            conds.append(SecondarySale.doctor_id == self.doctor_id)
        if self.medical_store_id is not None:
            conds.append(SecondarySale.medical_store_id == self.medical_store_id)
        return and_(*conds)


class DashboardRepository:
    """All queries here return raw aggregates; the service decorates them."""

    # ------------------------------------------------------------------
    # KPI totals
    # ------------------------------------------------------------------

    async def totals(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        filters: DashboardFilters,
    ) -> tuple[float, int, int]:
        """Returns (revenue, sale_qty, line_count)."""
        if not mr_ids:
            return 0.0, 0, 0
        clause = filters.apply(mr_ids, date_from, date_to)
        stmt = select(
            func.coalesce(func.sum(SecondarySale.total_amount), 0),
            func.coalesce(func.sum(SecondarySale.sale_qty), 0),
            func.count(SecondarySale.id),
        ).where(clause)
        row = (await db.execute(stmt)).one()
        return float(row[0] or 0.0), int(row[1] or 0), int(row[2] or 0)

    # ------------------------------------------------------------------
    # Time series (bucketed)
    # ------------------------------------------------------------------

    async def trend(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        bucket: str,
        filters: DashboardFilters,
    ) -> list[tuple[str, float, int]]:
        """Returns (period_key_iso, revenue, sale_qty) ordered by period."""
        if not mr_ids:
            return []
        clause = filters.apply(mr_ids, date_from, date_to)
        if bucket not in ("month", "quarter", "year"):
            raise ValueError("bucket must be month, quarter or year")
        sd = SecondarySale.sale_date
        period_expr = func.date_trunc(bucket, cast(sd, TIMESTAMP()))
        stmt = (
            select(
                cast(period_expr, String).label("pk"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0),
                func.coalesce(func.sum(SecondarySale.sale_qty), 0),
            )
            .where(clause)
            .group_by(period_expr)
            .order_by(period_expr)
        )
        rows = (await db.execute(stmt)).all()
        return [(str(r[0]), float(r[1] or 0), int(r[2] or 0)) for r in rows]

    # ------------------------------------------------------------------
    # Top N entities
    # ------------------------------------------------------------------

    async def top_products(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        filters: DashboardFilters,
        limit: int,
    ) -> list[tuple[uuid.UUID, str, float, int]]:
        if not mr_ids:
            return []
        clause = filters.apply(mr_ids, date_from, date_to)
        agg = (
            select(
                SecondarySale.product_id.label("pid"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0).label("rev"),
                func.coalesce(func.sum(SecondarySale.sale_qty), 0).label("qty"),
            )
            .where(clause)
            .group_by(SecondarySale.product_id)
        ).subquery()
        stmt = (
            select(agg.c.pid, Product.name, agg.c.rev, agg.c.qty)
            .join(Product, Product.id == agg.c.pid)
            .order_by(agg.c.rev.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        return [(r[0], str(r[1]), float(r[2] or 0), int(r[3] or 0)) for r in rows]

    async def top_doctors(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        filters: DashboardFilters,
        limit: int,
    ) -> list[tuple[uuid.UUID, str, float, int]]:
        if not mr_ids:
            return []
        clause = filters.apply(mr_ids, date_from, date_to)
        clause = and_(clause, SecondarySale.doctor_id.is_not(None))
        agg = (
            select(
                SecondarySale.doctor_id.label("did"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0).label("rev"),
                func.coalesce(func.sum(SecondarySale.sale_qty), 0).label("qty"),
            )
            .where(clause)
            .group_by(SecondarySale.doctor_id)
        ).subquery()
        stmt = (
            select(agg.c.did, Doctor.full_name, agg.c.rev, agg.c.qty)
            .join(Doctor, Doctor.id == agg.c.did)
            .order_by(agg.c.rev.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        return [(r[0], str(r[1]), float(r[2] or 0), int(r[3] or 0)) for r in rows]

    async def top_medical_stores(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        filters: DashboardFilters,
        limit: int,
    ) -> list[tuple[uuid.UUID, str, float, int]]:
        if not mr_ids:
            return []
        clause = filters.apply(mr_ids, date_from, date_to)
        clause = and_(clause, SecondarySale.medical_store_id.is_not(None))
        agg = (
            select(
                SecondarySale.medical_store_id.label("sid"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0).label("rev"),
                func.coalesce(func.sum(SecondarySale.sale_qty), 0).label("qty"),
            )
            .where(clause)
            .group_by(SecondarySale.medical_store_id)
        ).subquery()
        stmt = (
            select(agg.c.sid, MedicalStore.name, agg.c.rev, agg.c.qty)
            .join(MedicalStore, MedicalStore.id == agg.c.sid)
            .order_by(agg.c.rev.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        return [(r[0], str(r[1]), float(r[2] or 0), int(r[3] or 0)) for r in rows]

    # ------------------------------------------------------------------
    # Growth (period vs prior period, per product)
    # ------------------------------------------------------------------

    async def product_revenue_map(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        filters: DashboardFilters,
    ) -> dict[uuid.UUID, tuple[str, float]]:
        """Returns {product_id: (product_name, revenue)} for the period.

        Includes products with zero revenue only if they appear in the date window;
        otherwise the service has to merge with the comparison period to find
        new/dropped products.
        """
        if not mr_ids:
            return {}
        clause = filters.apply(mr_ids, date_from, date_to)
        agg = (
            select(
                SecondarySale.product_id.label("pid"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0).label("rev"),
            )
            .where(clause)
            .group_by(SecondarySale.product_id)
        ).subquery()
        stmt = select(agg.c.pid, Product.name, agg.c.rev).join(
            Product, Product.id == agg.c.pid
        )
        rows = (await db.execute(stmt)).all()
        return {r[0]: (str(r[1]), float(r[2] or 0)) for r in rows}

    # ------------------------------------------------------------------
    # Filter source lookups (label maps for FE dropdowns)
    # ------------------------------------------------------------------

    async def distinct_dim_ids(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        column: Any,
        active_only: bool = True,
    ) -> list[uuid.UUID]:
        """Returns distinct non-null IDs for the given SecondarySale column."""
        if not mr_ids:
            return []
        clauses = [
            SecondarySale.mr_id.in_(mr_ids),
            SecondarySale.sale_date >= date_from,
            SecondarySale.sale_date <= date_to,
            column.is_not(None),
        ]
        if active_only:
            clauses.append(SecondarySale.is_active.is_(True))
        stmt = select(column).where(and_(*clauses)).distinct()
        rows = (await db.execute(stmt)).all()
        return [r[0] for r in rows if r[0] is not None]

    async def names_for_states(
        self, db: AsyncSession, ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        if not ids:
            return []
        stmt = select(State.id, State.name, State.code).where(State.id.in_(ids))
        return [(r[0], r[1], r[2]) for r in (await db.execute(stmt)).all()]

    async def names_for_headquarters(
        self, db: AsyncSession, ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        if not ids:
            return []
        stmt = select(Headquarter.id, Headquarter.name, Headquarter.code).where(
            Headquarter.id.in_(ids)
        )
        return [(r[0], r[1], r[2]) for r in (await db.execute(stmt)).all()]

    async def names_for_divisions(
        self, db: AsyncSession, ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        if not ids:
            return []
        stmt = select(Division.id, Division.name, Division.code).where(
            Division.id.in_(ids)
        )
        return [(r[0], r[1], r[2]) for r in (await db.execute(stmt)).all()]

    async def names_for_products(
        self, db: AsyncSession, ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        if not ids:
            return []
        stmt = select(Product.id, Product.name, Product.pack_size).where(
            Product.id.in_(ids)
        )
        return [(r[0], r[1], r[2]) for r in (await db.execute(stmt)).all()]

    async def names_for_doctors(
        self, db: AsyncSession, ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        if not ids:
            return []
        stmt = select(Doctor.id, Doctor.full_name, Doctor.specialization).where(
            Doctor.id.in_(ids)
        )
        return [(r[0], r[1], r[2]) for r in (await db.execute(stmt)).all()]

    async def names_for_medical_stores(
        self, db: AsyncSession, ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        if not ids:
            return []
        stmt = select(MedicalStore.id, MedicalStore.name, MedicalStore.unique_code).where(
            MedicalStore.id.in_(ids)
        )
        return [(r[0], r[1], r[2]) for r in (await db.execute(stmt)).all()]
