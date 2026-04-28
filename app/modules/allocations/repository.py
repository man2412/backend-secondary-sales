import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrDoctorAllocation, MrLocationAllocation, MrStoreAllocation
from app.models.doctor import Doctor
from app.models.master import Location
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

    async def get_store_alloc(self, db: AsyncSession, alloc_id: uuid.UUID) -> MrStoreAllocation | None:
        r = await db.execute(select(MrStoreAllocation).where(MrStoreAllocation.id == alloc_id))
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

    async def list_store_alloc_rows(
        self, db: AsyncSession, mr_id: uuid.UUID, active_only: bool
    ) -> Sequence[MrStoreAllocation]:
        q = select(MrStoreAllocation).where(MrStoreAllocation.mr_id == mr_id)
        if active_only:
            q = q.where(MrStoreAllocation.is_active.is_(True))
        q = q.order_by(MrStoreAllocation.allocated_at.desc())
        return (await db.execute(q)).scalars().all()

    async def location_name(self, db: AsyncSession, location_id: uuid.UUID) -> str | None:
        r = await db.execute(select(Location.name).where(Location.id == location_id))
        row = r.one_or_none()
        return row[0] if row else None

    async def doctor_name(self, db: AsyncSession, doctor_id: uuid.UUID) -> str | None:
        r = await db.execute(select(Doctor.full_name).where(Doctor.id == doctor_id))
        row = r.one_or_none()
        return row[0] if row else None

    async def store_name(self, db: AsyncSession, store_id: uuid.UUID) -> str | None:
        r = await db.execute(select(MedicalStore.name).where(MedicalStore.id == store_id))
        row = r.one_or_none()
        return row[0] if row else None

    async def location_names_map(
        self, db: AsyncSession, location_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        if not location_ids:
            return {}
        r = await db.execute(select(Location.id, Location.name).where(Location.id.in_(location_ids)))
        return {row[0]: row[1] for row in r.all()}

    async def doctor_names_map(
        self, db: AsyncSession, doctor_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        if not doctor_ids:
            return {}
        r = await db.execute(select(Doctor.id, Doctor.full_name).where(Doctor.id.in_(doctor_ids)))
        return {row[0]: row[1] for row in r.all()}

    async def store_names_map(
        self, db: AsyncSession, store_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        if not store_ids:
            return {}
        r = await db.execute(select(MedicalStore.id, MedicalStore.name).where(MedicalStore.id.in_(store_ids)))
        return {row[0]: row[1] for row in r.all()}

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
        allocated_by: uuid.UUID,
    ) -> MrDoctorAllocation:
        r = await db.execute(
            select(MrDoctorAllocation).where(
                MrDoctorAllocation.mr_id == mr_id,
                MrDoctorAllocation.doctor_id == doctor_id,
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
            allocated_by=allocated_by,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def upsert_store_alloc(
        self,
        db: AsyncSession,
        *,
        mr_id: uuid.UUID,
        medical_store_id: uuid.UUID,
        allocated_by: uuid.UUID,
    ) -> MrStoreAllocation:
        r = await db.execute(
            select(MrStoreAllocation).where(
                MrStoreAllocation.mr_id == mr_id,
                MrStoreAllocation.medical_store_id == medical_store_id,
            )
        )
        existing = r.scalar_one_or_none()
        if existing is not None:
            existing.is_active = True
            existing.allocated_by = allocated_by
            await db.flush()
            await db.refresh(existing)
            return existing
        row = MrStoreAllocation(
            mr_id=mr_id,
            medical_store_id=medical_store_id,
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

    async def soft_delete_store(self, db: AsyncSession, row: MrStoreAllocation) -> None:
        row.is_active = False
        await db.flush()
        await db.refresh(row)
