import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrDoctorAllocation
from app.models.doctor import Doctor, DoctorMedicalStore
from app.models.master import Headquarter, Location, State


class DoctorsRepository:
    async def get_doctor(self, db: AsyncSession, doctor_id: uuid.UUID) -> Doctor | None:
        r = await db.execute(select(Doctor).where(Doctor.id == doctor_id))
        return r.scalar_one_or_none()

    async def list_medical_store_ids(self, db: AsyncSession, doctor_id: uuid.UUID) -> list[uuid.UUID]:
        r = await db.execute(
            select(DoctorMedicalStore.medical_store_id).where(DoctorMedicalStore.doctor_id == doctor_id)
        )
        return [row[0] for row in r.fetchall()]

    async def set_medical_store_links(
        self, db: AsyncSession, doctor_id: uuid.UUID, store_ids: list[uuid.UUID]
    ) -> None:
        await db.execute(delete(DoctorMedicalStore).where(DoctorMedicalStore.doctor_id == doctor_id))
        for sid in store_ids:
            db.add(DoctorMedicalStore(doctor_id=doctor_id, medical_store_id=sid))
        await db.flush()

    async def list_doctors(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        location_id: uuid.UUID | None,
        active_only: bool,
        mr_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Doctor], int]:
        base = select(Doctor)
        count_q = select(func.count()).select_from(Doctor)
        if active_only:
            base = base.where(Doctor.is_active.is_(True))
            count_q = count_q.where(Doctor.is_active.is_(True))
        if location_id is not None:
            base = base.where(Doctor.location_id == location_id)
            count_q = count_q.where(Doctor.location_id == location_id)
        if mr_id is not None:
            subq = select(MrDoctorAllocation.doctor_id).where(
                MrDoctorAllocation.mr_id == mr_id,
                MrDoctorAllocation.is_active.is_(True),
            )
            base = base.where(Doctor.id.in_(subq))
            count_q = count_q.where(Doctor.id.in_(subq))
        if q is not None and q.strip():
            term = f"%{q.strip()}%"
            base = base.where((Doctor.full_name.ilike(term)) | (Doctor.phone.ilike(term)))
            count_q = count_q.where((Doctor.full_name.ilike(term)) | (Doctor.phone.ilike(term)))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(Doctor.full_name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_doctor(
        self,
        db: AsyncSession,
        *,
        full_name: str,
        specialization: str | None,
        qualification: str | None,
        phone: str | None,
        address: str | None,
        location_id: uuid.UUID | None,
    ) -> Doctor:
        row = Doctor(
            full_name=full_name,
            specialization=specialization,
            qualification=qualification,
            phone=phone,
            address=address,
            location_id=location_id,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_doctor(
        self,
        db: AsyncSession,
        row: Doctor,
        *,
        full_name: str | None,
        specialization: str | None,
        qualification: str | None,
        phone: str | None,
        address: str | None,
        location_id: uuid.UUID | None,
        is_active: bool | None,
    ) -> Doctor:
        if full_name is not None:
            row.full_name = full_name
        if specialization is not None:
            row.specialization = specialization
        if qualification is not None:
            row.qualification = qualification
        if phone is not None:
            row.phone = phone
        if address is not None:
            row.address = address
        if location_id is not None:
            row.location_id = location_id
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row
