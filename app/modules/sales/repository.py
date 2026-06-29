import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.master import Headquarter, Location
from app.models.sale import SecondarySale


class SalesRepository:
    async def get_headquarter_context(
        self, db: AsyncSession, headquarter_id: uuid.UUID
    ) -> tuple[uuid.UUID, list[uuid.UUID]] | None:
        """Returns (state_id, division_ids[]) for the headquarter, or None if not found.

        A headquarter carries an array of divisions it serves; a sale's product
        must belong to one of those divisions. The caller derives `division_id`
        from the product itself, then sanity-checks it against this list.
        """
        stmt = select(Headquarter.state_id, Headquarter.division_ids).where(
            Headquarter.id == headquarter_id
        )
        r = await db.execute(stmt)
        row = r.one_or_none()
        if row is None:
            return None
        return (row[0], list(row[1] or []))

    async def location_belongs_to_headquarter(
        self, db: AsyncSession, location_id: uuid.UUID, headquarter_id: uuid.UUID
    ) -> bool:
        r = await db.execute(
            select(Location.id).where(
                Location.id == location_id,
                Location.headquarter_id == headquarter_id,
            )
        )
        return r.one_or_none() is not None

    async def get_sale(self, db: AsyncSession, sale_id: uuid.UUID) -> SecondarySale | None:
        r = await db.execute(select(SecondarySale).where(SecondarySale.id == sale_id))
        return r.scalar_one_or_none()

    async def list_sales(
        self,
        db: AsyncSession,
        *,
        caller_id: uuid.UUID,
        caller_role: UserRole,
        fallback_mr_ids: list[uuid.UUID],
        mr_id_filter: uuid.UUID | None,
        doctor_id_filter: uuid.UUID | None = None,
        product_id_filter: uuid.UUID | None = None,
        sale_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[SecondarySale], int]:
        """
        Role-aware sale listing with manager-snapshot RBAC.

        - SUPER_ADMIN, SALES_DIRECTOR: see all sales.
        - MR: only their own sales.
        - ASM/RSM/DEPUTY_RSM/STATE_HEAD: sales whose snapshot column matches
          their id. For older rows where the snapshot is NULL we fall back to
          the live subtree (`fallback_mr_ids`) so legacy data stays visible.
        """
        base = select(SecondarySale)
        count_q = select(func.count()).select_from(SecondarySale)

        # Collect every filter clause once and apply the identical set to both
        # the data query and the count query — they must always agree, and any
        # combination of filters is ANDed together.
        clauses = []
        rbac_clause = self._build_rbac_clause(caller_id, caller_role, fallback_mr_ids)
        if rbac_clause is not None:
            clauses.append(rbac_clause)
        if mr_id_filter is not None:
            clauses.append(SecondarySale.mr_id == mr_id_filter)
        if doctor_id_filter is not None:
            clauses.append(SecondarySale.doctor_id == doctor_id_filter)
        if product_id_filter is not None:
            clauses.append(SecondarySale.product_id == product_id_filter)
        if sale_date is not None:
            clauses.append(SecondarySale.sale_date == sale_date)
        if date_from is not None:
            clauses.append(SecondarySale.sale_date >= date_from)
        if date_to is not None:
            clauses.append(SecondarySale.sale_date <= date_to)
        if active_only:
            clauses.append(SecondarySale.is_active.is_(True))

        for c in clauses:
            base = base.where(c)
            count_q = count_q.where(c)

        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(SecondarySale.sale_date.desc(), SecondarySale.created_at.desc())
        base = base.offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    @staticmethod
    def _build_rbac_clause(
        caller_id: uuid.UUID,
        caller_role: UserRole,
        fallback_mr_ids: list[uuid.UUID],
    ):
        """Build the WHERE clause that scopes sales to what the caller can see."""
        if caller_role in (UserRole.SUPER_ADMIN, UserRole.SALES_DIRECTOR):
            return None
        if caller_role == UserRole.MR:
            return SecondarySale.mr_id == caller_id

        # Manager roles: prefer snapshot column; fall back to live subtree for
        # rows persisted before the snapshot columns existed (snapshot IS NULL).
        if caller_role == UserRole.ASM:
            snapshot_col = SecondarySale.asm_id
        elif caller_role in (UserRole.RSM, UserRole.DEPUTY_RSM):
            snapshot_col = SecondarySale.rsm_id
        elif caller_role == UserRole.STATE_HEAD:
            snapshot_col = SecondarySale.state_head_id
        else:
            # Unknown role — deny by default with an impossible filter.
            return SecondarySale.mr_id.in_([])

        clauses = [snapshot_col == caller_id]
        if fallback_mr_ids:
            clauses.append(
                and_(snapshot_col.is_(None), SecondarySale.mr_id.in_(fallback_mr_ids))
            )
        return or_(*clauses)

    async def count_mr_sales_on_date(
        self, db: AsyncSession, *, mr_id: uuid.UUID, sale_date: date
    ) -> int:
        q = select(func.count()).select_from(SecondarySale).where(
            SecondarySale.mr_id == mr_id,
            SecondarySale.sale_date == sale_date,
            SecondarySale.is_active.is_(True),
        )
        return int((await db.execute(q)).scalar_one())

    async def create_sale(
        self,
        db: AsyncSession,
        *,
        mr_id: uuid.UUID,
        product_id: uuid.UUID,
        doctor_id: uuid.UUID | None,
        medical_store_id: uuid.UUID | None,
        division_id: uuid.UUID,
        headquarter_id: uuid.UUID,
        location_id: uuid.UUID | None,
        state_id: uuid.UUID,
        sale_date: date,
        sale_qty: int,
        free_qty: int,
        ptr: float,
        pts: float,
        mrp: float,
        special_price: float | None,
        remarks: str | None,
        asm_id: uuid.UUID | None = None,
        rsm_id: uuid.UUID | None = None,
        state_head_id: uuid.UUID | None = None,
    ) -> SecondarySale:
        row = SecondarySale(
            mr_id=mr_id,
            asm_id=asm_id,
            rsm_id=rsm_id,
            state_head_id=state_head_id,
            product_id=product_id,
            doctor_id=doctor_id,
            medical_store_id=medical_store_id,
            division_id=division_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            state_id=state_id,
            sale_date=sale_date,
            sale_qty=sale_qty,
            free_qty=free_qty,
            ptr=ptr,
            pts=pts,
            mrp=mrp,
            special_price=special_price,
            remarks=remarks,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_sale(self, db: AsyncSession, row: SecondarySale, patch: dict) -> SecondarySale:
        if "sale_qty" in patch:
            row.sale_qty = patch["sale_qty"]
        if "free_qty" in patch:
            row.free_qty = patch["free_qty"]
        if "special_price" in patch:
            row.special_price = patch["special_price"]
        if "remarks" in patch:
            row.remarks = patch["remarks"]
        await db.flush()
        await db.refresh(row)
        return row

    async def soft_delete_sale(self, db: AsyncSession, row: SecondarySale) -> None:
        row.is_active = False
        await db.flush()
        await db.refresh(row)
