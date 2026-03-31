import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.master import Headquarter, Location, State
from app.models.sale import SecondarySale


class SalesRepository:
    async def get_location_context(
        self, db: AsyncSession, location_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID] | None:
        """Returns (state_id, company_id, headquarter_id, division_id)."""
        stmt = (
            select(
                State.id,
                State.company_id,
                Headquarter.id,
                Headquarter.division_id,
            )
            .select_from(Location)
            .join(Headquarter, Location.headquarter_id == Headquarter.id)
            .join(State, Headquarter.state_id == State.id)
            .where(Location.id == location_id)
        )
        r = await db.execute(stmt)
        row = r.one_or_none()
        if row is None:
            return None
        return (row[0], row[1], row[2], row[3])

    async def get_sale(self, db: AsyncSession, sale_id: uuid.UUID) -> SecondarySale | None:
        r = await db.execute(select(SecondarySale).where(SecondarySale.id == sale_id))
        return r.scalar_one_or_none()

    async def list_sales(
        self,
        db: AsyncSession,
        *,
        mr_ids: list[uuid.UUID],
        sale_date: date | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[SecondarySale], int]:
        base = select(SecondarySale).where(SecondarySale.mr_id.in_(mr_ids))
        count_q = select(func.count()).select_from(SecondarySale).where(SecondarySale.mr_id.in_(mr_ids))
        if sale_date is not None:
            base = base.where(SecondarySale.sale_date == sale_date)
            count_q = count_q.where(SecondarySale.sale_date == sale_date)
        if active_only:
            base = base.where(SecondarySale.is_active.is_(True))
            count_q = count_q.where(SecondarySale.is_active.is_(True))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(SecondarySale.sale_date.desc(), SecondarySale.created_at.desc())
        base = base.offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

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
        location_id: uuid.UUID,
        state_id: uuid.UUID,
        company_id: uuid.UUID,
        sale_date: date,
        sale_qty: int,
        free_qty: int,
        ptr: float,
        pts: float,
        mrp: float,
        special_price: float | None,
        remarks: str | None,
    ) -> SecondarySale:
        row = SecondarySale(
            mr_id=mr_id,
            product_id=product_id,
            doctor_id=doctor_id,
            medical_store_id=medical_store_id,
            division_id=division_id,
            headquarter_id=headquarter_id,
            location_id=location_id,
            state_id=state_id,
            company_id=company_id,
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
