import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrStoreAllocation
from app.models.stockist import MedicalStore, Stockist, SuperStockist


class StockistsRepository:
    # --- Super stockist ---

    async def get_super_stockist(self, db: AsyncSession, entity_id: uuid.UUID) -> SuperStockist | None:
        r = await db.execute(select(SuperStockist).where(SuperStockist.id == entity_id))
        return r.scalar_one_or_none()

    async def list_super_stockists(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[SuperStockist], int]:
        base = select(SuperStockist)
        count_q = select(func.count()).select_from(SuperStockist)
        if active_only:
            base = base.where(SuperStockist.is_active.is_(True))
            count_q = count_q.where(SuperStockist.is_active.is_(True))
        if q is not None and q.strip():
            term = f"%{q.strip()}%"
            base = base.where(
                (SuperStockist.name.ilike(term))
                | (SuperStockist.gst_number.ilike(term))
                | (SuperStockist.pan.ilike(term))
                | (SuperStockist.unique_code.ilike(term))
            )
            count_q = count_q.where(
                (SuperStockist.name.ilike(term))
                | (SuperStockist.gst_number.ilike(term))
                | (SuperStockist.pan.ilike(term))
                | (SuperStockist.unique_code.ilike(term))
            )
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(SuperStockist.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_super_stockist(
        self,
        db: AsyncSession,
        *,
        name: str,
        unique_code: str | None,
        gst_number: str | None,
        drug_licence: str | None,
        pan: str | None,
        address: str | None,
    ) -> SuperStockist:
        row = SuperStockist(
            name=name,
            unique_code=unique_code,
            gst_number=gst_number,
            drug_licence=drug_licence,
            pan=pan,
            address=address,
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
        q: str | None,
        super_stockist_id: uuid.UUID | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Stockist], int]:
        base = select(Stockist)
        count_q = select(func.count()).select_from(Stockist)
        if active_only:
            base = base.where(Stockist.is_active.is_(True))
            count_q = count_q.where(Stockist.is_active.is_(True))
        if super_stockist_id is not None:
            base = base.where(Stockist.super_stockist_id == super_stockist_id)
            count_q = count_q.where(Stockist.super_stockist_id == super_stockist_id)
        if q is not None and q.strip():
            term = f"%{q.strip()}%"
            base = base.where(
                (Stockist.name.ilike(term))
                | (Stockist.gst_number.ilike(term))
                | (Stockist.pan.ilike(term))
                | (Stockist.unique_code.ilike(term))
            )
            count_q = count_q.where(
                (Stockist.name.ilike(term))
                | (Stockist.gst_number.ilike(term))
                | (Stockist.pan.ilike(term))
                | (Stockist.unique_code.ilike(term))
            )
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(Stockist.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_stockist(
        self,
        db: AsyncSession,
        *,
        super_stockist_id: uuid.UUID | None,
        name: str,
        unique_code: str | None,
        gst_number: str | None,
        drug_licence: str | None,
        pan: str | None,
        address: str | None,
    ) -> Stockist:
        row = Stockist(
            super_stockist_id=super_stockist_id,
            name=name,
            unique_code=unique_code,
            gst_number=gst_number,
            drug_licence=drug_licence,
            pan=pan,
            address=address,
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
        q: str | None,
        stockist_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        active_only: bool,
        mr_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[MedicalStore], int]:
        base = select(MedicalStore)
        count_q = select(func.count()).select_from(MedicalStore)
        if active_only:
            base = base.where(MedicalStore.is_active.is_(True))
            count_q = count_q.where(MedicalStore.is_active.is_(True))
        if stockist_id is not None:
            base = base.where(MedicalStore.stockist_id == stockist_id)
            count_q = count_q.where(MedicalStore.stockist_id == stockist_id)
        if headquarter_id is not None:
            base = base.where(MedicalStore.headquarter_id == headquarter_id)
            count_q = count_q.where(MedicalStore.headquarter_id == headquarter_id)
        if mr_id is not None:
            subq = select(MrStoreAllocation.medical_store_id).where(
                MrStoreAllocation.mr_id == mr_id,
                MrStoreAllocation.is_active.is_(True),
            )
            base = base.where(MedicalStore.id.in_(subq))
            count_q = count_q.where(MedicalStore.id.in_(subq))
        if q is not None and q.strip():
            term = f"%{q.strip()}%"
            base = base.where(
                (MedicalStore.name.ilike(term))
                | (MedicalStore.gst_number.ilike(term))
                | (MedicalStore.pan.ilike(term))
                | (MedicalStore.unique_code.ilike(term))
            )
            count_q = count_q.where(
                (MedicalStore.name.ilike(term))
                | (MedicalStore.gst_number.ilike(term))
                | (MedicalStore.pan.ilike(term))
                | (MedicalStore.unique_code.ilike(term))
            )
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(MedicalStore.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_medical_store(
        self,
        db: AsyncSession,
        *,
        stockist_id: uuid.UUID | None,
        name: str,
        unique_code: str | None,
        gst_number: str | None,
        drug_licence: str | None,
        pan: str | None,
        address: str | None,
        headquarter_id: uuid.UUID | None,
        alternate_names: list[str] | None,
    ) -> MedicalStore:
        row = MedicalStore(
            stockist_id=stockist_id,
            name=name,
            unique_code=unique_code,
            gst_number=gst_number,
            drug_licence=drug_licence,
            pan=pan,
            address=address,
            headquarter_id=headquarter_id,
            alternate_names=alternate_names,
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
        headquarter_id: uuid.UUID | None,
        alternate_names: list[str] | None,
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
        if headquarter_id is not None:
            row.headquarter_id = headquarter_id
        if alternate_names is not None:
            row.alternate_names = alternate_names
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row
