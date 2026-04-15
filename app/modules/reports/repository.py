import uuid
from datetime import date
from typing import Any

from sqlalchemy import String, and_, cast, func, select, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.master import Division, Headquarter, Location, Product
from app.models.sale import SecondarySale
from app.models.user import User


class ReportsRepository:
    def _dim_conditions(
        self,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        *,
        active_only: bool,
        doctor_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        division_id: uuid.UUID | None,
        state_id: uuid.UUID | None,
    ):
        conds = [
            SecondarySale.mr_id.in_(mr_ids),
            SecondarySale.sale_date >= date_from,
            SecondarySale.sale_date <= date_to,
        ]
        if active_only:
            conds.append(SecondarySale.is_active.is_(True))
        if doctor_id is not None:
            conds.append(SecondarySale.doctor_id == doctor_id)
        if headquarter_id is not None:
            conds.append(SecondarySale.headquarter_id == headquarter_id)
        if location_id is not None:
            conds.append(SecondarySale.location_id == location_id)
        if product_id is not None:
            conds.append(SecondarySale.product_id == product_id)
        if division_id is not None:
            conds.append(SecondarySale.division_id == division_id)
        if state_id is not None:
            conds.append(SecondarySale.state_id == state_id)
        return and_(*conds)

    async def analytics_summary(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        active_only: bool,
        doctor_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        division_id: uuid.UUID | None,
        state_id: uuid.UUID | None,
    ) -> tuple[int, int, int, float]:
        if not mr_ids:
            return 0, 0, 0, 0.0
        filt = self._dim_conditions(
            mr_ids,
            date_from,
            date_to,
            active_only=active_only,
            doctor_id=doctor_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            product_id=product_id,
            division_id=division_id,
            state_id=state_id,
        )
        stmt = select(
            func.count(SecondarySale.id),
            func.coalesce(func.sum(SecondarySale.sale_qty), 0),
            func.coalesce(func.sum(SecondarySale.free_qty), 0),
            func.coalesce(func.sum(SecondarySale.total_amount), 0),
        ).where(filt)
        r = await db.execute(stmt)
        row = r.one()
        return int(row[0]), int(row[1] or 0), int(row[2] or 0), float(row[3] or 0)

    async def analytics_timeseries(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        active_only: bool,
        doctor_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        division_id: uuid.UUID | None,
        state_id: uuid.UUID | None,
        bucket: str,
    ) -> list[tuple[str, float, int, int]]:
        """Returns (period_key, revenue, sale_qty, free_qty)."""
        if not mr_ids:
            return []
        filt = self._dim_conditions(
            mr_ids,
            date_from,
            date_to,
            active_only=active_only,
            doctor_id=doctor_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            product_id=product_id,
            division_id=division_id,
            state_id=state_id,
        )
        sd = SecondarySale.sale_date
        if bucket == "day":
            period_expr = sd
            label_expr = cast(sd, String)
        elif bucket == "week":
            period_expr = func.date_trunc("week", cast(sd, TIMESTAMP()))
            label_expr = cast(func.date_trunc("week", cast(sd, TIMESTAMP())), String)
        elif bucket == "month":
            period_expr = func.date_trunc("month", cast(sd, TIMESTAMP()))
            label_expr = cast(func.date_trunc("month", cast(sd, TIMESTAMP())), String)
        else:
            period_expr = sd
            label_expr = cast(sd, String)

        stmt = (
            select(
                label_expr.label("pk"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0),
                func.coalesce(func.sum(SecondarySale.sale_qty), 0),
                func.coalesce(func.sum(SecondarySale.free_qty), 0),
            )
            .where(filt)
            .group_by(period_expr)
            .order_by(period_expr)
        )
        rows = (await db.execute(stmt)).all()
        out: list[tuple[str, float, int, int]] = []
        for pk, rev, sq, fq in rows:
            out.append((str(pk), float(rev or 0), int(sq or 0), int(fq or 0)))
        return out

    async def analytics_pie_by_product(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        active_only: bool,
        doctor_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        division_id: uuid.UUID | None,
        state_id: uuid.UUID | None,
    ) -> list[tuple[uuid.UUID, str, float, int]]:
        if not mr_ids:
            return []
        filt = self._dim_conditions(
            mr_ids,
            date_from,
            date_to,
            active_only=active_only,
            doctor_id=doctor_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            product_id=product_id,
            division_id=division_id,
            state_id=state_id,
        )
        agg = (
            select(
                SecondarySale.product_id.label("pid"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0).label("ta"),
                func.coalesce(func.sum(SecondarySale.sale_qty), 0).label("sq"),
            )
            .where(filt)
            .group_by(SecondarySale.product_id)
        ).subquery()
        stmt = select(agg.c.pid, Product.name, agg.c.ta, agg.c.sq).join(Product, Product.id == agg.c.pid).order_by(
            agg.c.ta.desc()
        )
        rows = (await db.execute(stmt)).all()
        return [(r[0], str(r[1]), float(r[2] or 0), int(r[3] or 0)) for r in rows]

    async def analytics_pie_by_location(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        active_only: bool,
        doctor_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        division_id: uuid.UUID | None,
        state_id: uuid.UUID | None,
    ) -> list[tuple[uuid.UUID, str, float, int]]:
        if not mr_ids:
            return []
        filt = self._dim_conditions(
            mr_ids,
            date_from,
            date_to,
            active_only=active_only,
            doctor_id=doctor_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            product_id=product_id,
            division_id=division_id,
            state_id=state_id,
        )
        agg = (
            select(
                SecondarySale.location_id.label("lid"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0).label("ta"),
                func.coalesce(func.sum(SecondarySale.sale_qty), 0).label("sq"),
            )
            .where(filt)
            .group_by(SecondarySale.location_id)
        ).subquery()
        stmt = select(agg.c.lid, Location.name, agg.c.ta, agg.c.sq).join(Location, Location.id == agg.c.lid).order_by(
            agg.c.ta.desc()
        )
        rows = (await db.execute(stmt)).all()
        return [(r[0], str(r[1]), float(r[2] or 0), int(r[3] or 0)) for r in rows]

    async def analytics_pie_by_headquarter(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        active_only: bool,
        doctor_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        division_id: uuid.UUID | None,
        state_id: uuid.UUID | None,
    ) -> list[tuple[uuid.UUID, str, float, int]]:
        if not mr_ids:
            return []
        filt = self._dim_conditions(
            mr_ids,
            date_from,
            date_to,
            active_only=active_only,
            doctor_id=doctor_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            product_id=product_id,
            division_id=division_id,
            state_id=state_id,
        )
        agg = (
            select(
                SecondarySale.headquarter_id.label("hid"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0).label("ta"),
                func.coalesce(func.sum(SecondarySale.sale_qty), 0).label("sq"),
            )
            .where(filt)
            .group_by(SecondarySale.headquarter_id)
        ).subquery()
        stmt = (
            select(agg.c.hid, Headquarter.name, agg.c.ta, agg.c.sq)
            .join(Headquarter, Headquarter.id == agg.c.hid)
            .order_by(agg.c.ta.desc())
        )
        rows = (await db.execute(stmt)).all()
        return [(r[0], str(r[1]), float(r[2] or 0), int(r[3] or 0)) for r in rows]

    async def analytics_pie_by_division(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        active_only: bool,
        doctor_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        division_id: uuid.UUID | None,
        state_id: uuid.UUID | None,
    ) -> list[tuple[uuid.UUID, str, float, int]]:
        if not mr_ids:
            return []
        filt = self._dim_conditions(
            mr_ids,
            date_from,
            date_to,
            active_only=active_only,
            doctor_id=doctor_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            product_id=product_id,
            division_id=division_id,
            state_id=state_id,
        )
        agg = (
            select(
                SecondarySale.division_id.label("did"),
                func.coalesce(func.sum(SecondarySale.total_amount), 0).label("ta"),
                func.coalesce(func.sum(SecondarySale.sale_qty), 0).label("sq"),
            )
            .where(filt)
            .group_by(SecondarySale.division_id)
        ).subquery()
        stmt = (
            select(agg.c.did, Division.name, agg.c.ta, agg.c.sq).join(Division, Division.id == agg.c.did).order_by(
                agg.c.ta.desc()
            )
        )
        rows = (await db.execute(stmt)).all()
        return [(r[0], str(r[1]), float(r[2] or 0), int(r[3] or 0)) for r in rows]

    async def analytics_pie_by_manager_role(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        active_only: bool,
        doctor_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        division_id: uuid.UUID | None,
        state_id: uuid.UUID | None,
        manager_role: str,
    ) -> list[tuple[uuid.UUID, str, float, int]]:
        """Aggregate sales under each RSM or ASM by subtree MRs. manager_role: 'RSM' or 'ASM'."""
        if not mr_ids:
            return []
        mr_in_list = ", ".join(f"'{uid}'::uuid" for uid in mr_ids)
        active_clause = "AND s.is_active = true" if active_only else ""
        dim_extra: list[str] = []
        params: dict[str, Any] = {
            "df": date_from,
            "dt": date_to,
            "mgr_role": manager_role,
        }
        if doctor_id is not None:
            dim_extra.append("AND s.doctor_id = CAST(:doctor_id AS uuid)")
            params["doctor_id"] = str(doctor_id)
        if headquarter_id is not None:
            dim_extra.append("AND s.headquarter_id = CAST(:headquarter_id AS uuid)")
            params["headquarter_id"] = str(headquarter_id)
        if location_id is not None:
            dim_extra.append("AND s.location_id = CAST(:location_id AS uuid)")
            params["location_id"] = str(location_id)
        if product_id is not None:
            dim_extra.append("AND s.product_id = CAST(:product_id AS uuid)")
            params["product_id"] = str(product_id)
        if division_id is not None:
            dim_extra.append("AND s.division_id = CAST(:division_id AS uuid)")
            params["division_id"] = str(division_id)
        if state_id is not None:
            dim_extra.append("AND s.state_id = CAST(:state_id AS uuid)")
            params["state_id"] = str(state_id)
        dim_sql = "\n                  ".join(dim_extra)

        q = text(
            f"""
            WITH RECURSIVE descend AS (
                SELECT u.id AS root_id, u.id AS node_id, u.role::text AS role
                FROM users u
                WHERE u.role = CAST(:mgr_role AS user_role)
                  AND u.is_active = true
                UNION ALL
                SELECT d.root_id, u.id, u.role::text
                FROM users u
                INNER JOIN descend d ON u.reports_to = d.node_id
                WHERE u.is_active = true
            ),
            sales_agg AS (
                SELECT d.root_id,
                       COALESCE(SUM(s.total_amount), 0)::double precision AS revenue,
                       COALESCE(SUM(s.sale_qty), 0)::bigint AS qty
                FROM descend d
                INNER JOIN secondary_sales s ON s.mr_id = d.node_id AND d.role = 'MR'
                WHERE s.sale_date >= :df AND s.sale_date <= :dt
                  {active_clause}
                  AND s.mr_id IN ({mr_in_list})
                  {dim_sql}
                GROUP BY d.root_id
            )
            SELECT u.id, u.full_name,
                   COALESCE(a.revenue, 0)::double precision,
                   COALESCE(a.qty, 0)::bigint
            FROM users u
            LEFT JOIN sales_agg a ON a.root_id = u.id
            WHERE u.role = CAST(:mgr_role AS user_role)
              AND u.is_active = true
            ORDER BY COALESCE(a.revenue, 0) DESC, u.full_name
            """
        )
        r = await db.execute(q, params)
        rows = r.fetchall()
        return [(uuid.UUID(str(row[0])), str(row[1]), float(row[2] or 0), int(row[3] or 0)) for row in rows]
