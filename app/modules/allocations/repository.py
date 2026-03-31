import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrDoctorAllocation, MrLocationAllocation, MrProductAllocation
from app.models.doctor import Doctor, DoctorMedicalStore
from app.models.master import Division, Location, Product
from app.models.stockist import MedicalStore


class AllocationsRepository:
    async def get_location_alloc(
        self, db: AsyncSession, alloc_id: uuid.UUID
    ) -> MrLocationAllocation | None:
        r = await db.execute(select(MrLocationAllocation).where(MrLocationAllocation.id == alloc_id))
        return r.scalar_one_or_none()

    async def get_doctor_alloc(self, db: AsyncSession, alloc_id: uuid.UUID) -> MrDoctorAllocation | None:
        r = await db.execute(select(MrDoctorAllocation).where(MrDoctorAllocation.id == alloc_id))
        return r.scalar_one_or_none()

    async def get_product_alloc(self, db: AsyncSession, alloc_id: uuid.UUID) -> MrProductAllocation | None:
        r = await db.execute(select(MrProductAllocation).where(MrProductAllocation.id == alloc_id))
        return r.scalar_one_or_none()

    async def list_location_alloc_rows(
        self, db: AsyncSession, mr_id: uuid.UUID, active_only: bool
    ) -> Sequence[MrLocationAllocation]:
        q = select(MrLocationAllocation).where(MrLocationAllocation.mr_id == mr_id)
        if active_only:
            q = q.where(MrLocationAllocation.is_active.is_(True))
        q = q.order_by(MrLocationAllocation.allocated_at.desc())
        return (await db.execute(q)).scalars().all()

    async def list_doctor_alloc_rows(
        self, db: AsyncSession, mr_id: uuid.UUID, active_only: bool
    ) -> Sequence[MrDoctorAllocation]:
        q = select(MrDoctorAllocation).where(MrDoctorAllocation.mr_id == mr_id)
        if active_only:
            q = q.where(MrDoctorAllocation.is_active.is_(True))
        q = q.order_by(MrDoctorAllocation.allocated_at.desc())
        return (await db.execute(q)).scalars().all()

    async def list_medical_stores_via_allocated_doctors(self, db: AsyncSession, mr_id: uuid.UUID, active_only: bool):
        """Each row: mr_doctor_alloc_id, doctor_id, division_id, medical_store_id, allocated_by, allocated_at, is_active."""
        q = (
            select(
                MrDoctorAllocation.id,
                MrDoctorAllocation.doctor_id,
                MrDoctorAllocation.division_id,
                DoctorMedicalStore.medical_store_id,
                MrDoctorAllocation.allocated_by,
                MrDoctorAllocation.allocated_at,
                MrDoctorAllocation.is_active,
            )
            .select_from(MrDoctorAllocation)
            .join(DoctorMedicalStore, DoctorMedicalStore.doctor_id == MrDoctorAllocation.doctor_id)
            .join(MedicalStore, MedicalStore.id == DoctorMedicalStore.medical_store_id)
            .where(MrDoctorAllocation.mr_id == mr_id)
        )
        if active_only:
            q = q.where(
                MrDoctorAllocation.is_active.is_(True),
                MedicalStore.is_active.is_(True),
            )
        q = q.order_by(MrDoctorAllocation.allocated_at.desc(), DoctorMedicalStore.medical_store_id)
        return (await db.execute(q)).all()

    async def list_product_alloc_rows(
        self, db: AsyncSession, mr_id: uuid.UUID, active_only: bool
    ) -> Sequence[MrProductAllocation]:
        q = select(MrProductAllocation).where(MrProductAllocation.mr_id == mr_id)
        if active_only:
            q = q.where(MrProductAllocation.is_active.is_(True))
        q = q.order_by(MrProductAllocation.allocated_at.desc())
        return (await db.execute(q)).scalars().all()

    async def location_name(self, db: AsyncSession, location_id: uuid.UUID) -> str | None:
        r = await db.execute(select(Location.name).where(Location.id == location_id))
        row = r.one_or_none()
        return row[0] if row else None

    async def doctor_name(self, db: AsyncSession, doctor_id: uuid.UUID) -> str | None:
        r = await db.execute(select(Doctor.full_name).where(Doctor.id == doctor_id))
        row = r.one_or_none()
        return row[0] if row else None

    async def division_name(self, db: AsyncSession, division_id: uuid.UUID) -> str | None:
        r = await db.execute(select(Division.name).where(Division.id == division_id))
        row = r.one_or_none()
        return row[0] if row else None

    async def store_name(self, db: AsyncSession, store_id: uuid.UUID) -> str | None:
        r = await db.execute(select(MedicalStore.name).where(MedicalStore.id == store_id))
        row = r.one_or_none()
        return row[0] if row else None

    async def product_name(self, db: AsyncSession, product_id: uuid.UUID) -> str | None:
        r = await db.execute(select(Product.name).where(Product.id == product_id))
        row = r.one_or_none()
        return row[0] if row else None

    async def upsert_location_alloc(
        self,
        db: AsyncSession,
        *,
        mr_id: uuid.UUID,
        location_id: uuid.UUID,
        allocated_by: uuid.UUID,
    ) -> MrLocationAllocation:
        r = await db.execute(
            select(MrLocationAllocation).where(
                MrLocationAllocation.mr_id == mr_id,
                MrLocationAllocation.location_id == location_id,
            )
        )
        existing = r.scalar_one_or_none()
        if existing is not None:
            existing.is_active = True
            existing.allocated_by = allocated_by
            await db.flush()
            await db.refresh(existing)
            return existing
        row = MrLocationAllocation(
            mr_id=mr_id,
            location_id=location_id,
            allocated_by=allocated_by,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def upsert_doctor_alloc(
        self,
        db: AsyncSession,
        *,
        mr_id: uuid.UUID,
        doctor_id: uuid.UUID,
        division_id: uuid.UUID,
        allocated_by: uuid.UUID,
    ) -> MrDoctorAllocation:
        r = await db.execute(
            select(MrDoctorAllocation).where(
                MrDoctorAllocation.mr_id == mr_id,
                MrDoctorAllocation.doctor_id == doctor_id,
                MrDoctorAllocation.division_id == division_id,
            )
        )
        existing = r.scalar_one_or_none()
        if existing is not None:
            existing.is_active = True
            existing.allocated_by = allocated_by
            await db.flush()
            await db.refresh(existing)
            return existing
        row = MrDoctorAllocation(
            mr_id=mr_id,
            doctor_id=doctor_id,
            division_id=division_id,
            allocated_by=allocated_by,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def upsert_product_alloc(
        self,
        db: AsyncSession,
        *,
        mr_id: uuid.UUID,
        product_id: uuid.UUID,
        allocated_by: uuid.UUID,
    ) -> MrProductAllocation:
        r = await db.execute(
            select(MrProductAllocation).where(
                MrProductAllocation.mr_id == mr_id,
                MrProductAllocation.product_id == product_id,
            )
        )
        existing = r.scalar_one_or_none()
        if existing is not None:
            existing.is_active = True
            existing.allocated_by = allocated_by
            await db.flush()
            await db.refresh(existing)
            return existing
        row = MrProductAllocation(
            mr_id=mr_id,
            product_id=product_id,
            allocated_by=allocated_by,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def soft_delete_location(self, db: AsyncSession, row: MrLocationAllocation) -> None:
        row.is_active = False
        await db.flush()
        await db.refresh(row)

    async def soft_delete_doctor(self, db: AsyncSession, row: MrDoctorAllocation) -> None:
        row.is_active = False
        await db.flush()
        await db.refresh(row)

    async def soft_delete_product(self, db: AsyncSession, row: MrProductAllocation) -> None:
        row.is_active = False
        await db.flush()
        await db.refresh(row)
