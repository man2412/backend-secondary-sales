import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrDoctorAllocation
from app.models.doctor import DoctorMedicalStore
from app.models.master import Headquarter, Location, State
from app.models.stockist import MedicalStore, Stockist, SuperStockist


class StockistsRepository:
    async def location_company_id(self, db: AsyncSession, location_id: uuid.UUID) -> uuid.UUID | None:
        r = await db.execute(
            select(State.company_id)
            .select_from(Location)
            .join(Headquarter, Location.headquarter_id == Headquarter.id)
            .join(State, Headquarter.state_id == State.id)
            .where(Location.id == location_id)
        )
        row = r.one_or_none()
        return row[0] if row else None

    # --- Super stockist ---

    async def get_super_stockist(self, db: AsyncSession, entity_id: uuid.UUID) -> SuperStockist | None:
        r = await db.execute(select(SuperStockist).where(SuperStockist.id == entity_id))
        return r.scalar_one_or_none()

    async def list_super_stockists(
        self,
        db: AsyncSession,
        *,
        company_id: uuid.UUID | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[SuperStockist], int]:
        base = select(SuperStockist)
        count_q = select(func.count()).select_from(SuperStockist)
        if company_id is not None:
            base = base.where(SuperStockist.company_id == company_id)
            count_q = count_q.where(SuperStockist.company_id == company_id)
        if active_only:
            base = base.where(SuperStockist.is_active.is_(True))
            count_q = count_q.where(SuperStockist.is_active.is_(True))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(SuperStockist.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_super_stockist(
        self,
        db: AsyncSession,
        *,
        company_id: uuid.UUID,
        name: str,
        unique_code: str | None,
        gst_number: str | None,
        drug_licence: str | None,
        pan: str | None,
        address: str | None,
        location_id: uuid.UUID | None,
    ) -> SuperStockist:
        row = SuperStockist(
            company_id=company_id,
            name=name,
            unique_code=unique_code,
            gst_number=gst_number,
            drug_licence=drug_licence,
            pan=pan,
            address=address,
            location_id=location_id,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_super_stockist(
        self,
        db: AsyncSession,
        row: SuperStockist,
        *,
        name: str | None,
        unique_code: str | None,
        gst_number: str | None,
        drug_licence: str | None,
        pan: str | None,
        address: str | None,
        location_id: uuid.UUID | None,
        is_active: bool | None,
    ) -> SuperStockist:
        if name is not None:
            row.name = name
        if unique_code is not None:
            row.unique_code = unique_code
        if gst_number is not None:
            row.gst_number = gst_number
        if drug_licence is not None:
            row.drug_licence = drug_licence
        if pan is not None:
            row.pan = pan
        if address is not None:
            row.address = address
        if location_id is not None:
            row.location_id = location_id
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row

    # --- Stockist ---

    async def get_stockist(self, db: AsyncSession, entity_id: uuid.UUID) -> Stockist | None:
        r = await db.execute(select(Stockist).where(Stockist.id == entity_id))
        return r.scalar_one_or_none()

    async def list_stockists(
        self,
        db: AsyncSession,
        *,
        company_id: uuid.UUID | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Stockist], int]:
        base = select(Stockist)
        count_q = select(func.count()).select_from(Stockist)
        if company_id is not None:
            base = base.where(Stockist.company_id == company_id)
            count_q = count_q.where(Stockist.company_id == company_id)
        if active_only:
            base = base.where(Stockist.is_active.is_(True))
            count_q = count_q.where(Stockist.is_active.is_(True))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(Stockist.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_stockist(
        self,
        db: AsyncSession,
        *,
        company_id: uuid.UUID,
        super_stockist_id: uuid.UUID | None,
        name: str,
        unique_code: str | None,
        gst_number: str | None,
        drug_licence: str | None,
        pan: str | None,
        address: str | None,
        location_id: uuid.UUID | None,
    ) -> Stockist:
        row = Stockist(
            company_id=company_id,
            super_stockist_id=super_stockist_id,
            name=name,
            unique_code=unique_code,
            gst_number=gst_number,
            drug_licence=drug_licence,
            pan=pan,
            address=address,
            location_id=location_id,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_stockist(
        self,
        db: AsyncSession,
        row: Stockist,
        *,
        super_stockist_id: uuid.UUID | None,
        name: str | None,
        unique_code: str | None,
        gst_number: str | None,
        drug_licence: str | None,
        pan: str | None,
        address: str | None,
        location_id: uuid.UUID | None,
        is_active: bool | None,
    ) -> Stockist:
        if super_stockist_id is not None:
            row.super_stockist_id = super_stockist_id
        if name is not None:
            row.name = name
        if unique_code is not None:
            row.unique_code = unique_code
        if gst_number is not None:
            row.gst_number = gst_number
        if drug_licence is not None:
            row.drug_licence = drug_licence
        if pan is not None:
            row.pan = pan
        if address is not None:
            row.address = address
        if location_id is not None:
            row.location_id = location_id
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row

    # --- Medical store ---

    async def get_medical_store(self, db: AsyncSession, entity_id: uuid.UUID) -> MedicalStore | None:
        r = await db.execute(select(MedicalStore).where(MedicalStore.id == entity_id))
        return r.scalar_one_or_none()

    async def list_medical_stores(
        self,
        db: AsyncSession,
        *,
        company_id: uuid.UUID | None,
        active_only: bool,
        mr_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[MedicalStore], int]:
        base = select(MedicalStore)
        count_q = select(func.count()).select_from(MedicalStore)
        if company_id is not None:
            base = base.where(MedicalStore.company_id == company_id)
            count_q = count_q.where(MedicalStore.company_id == company_id)
        if active_only:
            base = base.where(MedicalStore.is_active.is_(True))
            count_q = count_q.where(MedicalStore.is_active.is_(True))
        if mr_id is not None:
            subq = (
                select(DoctorMedicalStore.medical_store_id)
                .select_from(MrDoctorAllocation)
                .join(DoctorMedicalStore, DoctorMedicalStore.doctor_id == MrDoctorAllocation.doctor_id)
                .where(
                    MrDoctorAllocation.mr_id == mr_id,
                    MrDoctorAllocation.is_active.is_(True),
                )
                .distinct()
            )
            base = base.where(MedicalStore.id.in_(subq))
            count_q = count_q.where(MedicalStore.id.in_(subq))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(MedicalStore.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_medical_store(
        self,
        db: AsyncSession,
        *,
        company_id: uuid.UUID,
        stockist_id: uuid.UUID | None,
        name: str,
        unique_code: str | None,
        gst_number: str | None,
        drug_licence: str | None,
        pan: str | None,
        address: str | None,
        location_id: uuid.UUID | None,
    ) -> MedicalStore:
        row = MedicalStore(
            company_id=company_id,
            stockist_id=stockist_id,
            name=name,
            unique_code=unique_code,
            gst_number=gst_number,
            drug_licence=drug_licence,
            pan=pan,
            address=address,
            location_id=location_id,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_medical_store(
        self,
        db: AsyncSession,
        row: MedicalStore,
        *,
        stockist_id: uuid.UUID | None,
        name: str | None,
        unique_code: str | None,
        gst_number: str | None,
        drug_licence: str | None,
        pan: str | None,
        address: str | None,
        location_id: uuid.UUID | None,
        is_active: bool | None,
    ) -> MedicalStore:
        if stockist_id is not None:
            row.stockist_id = stockist_id
        if name is not None:
            row.name = name
        if unique_code is not None:
            row.unique_code = unique_code
        if gst_number is not None:
            row.gst_number = gst_number
        if drug_licence is not None:
            row.drug_licence = drug_licence
        if pan is not None:
            row.pan = pan
        if address is not None:
            row.address = address
        if location_id is not None:
            row.location_id = location_id
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row
