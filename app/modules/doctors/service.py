import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor import Doctor
from app.models.enums import UserRole
from app.models.user import User
from app.modules.doctors.repository import DoctorsRepository
from app.modules.doctors.schemas import DoctorCreate, DoctorOut, DoctorUpdate


class DoctorsService:
    def __init__(self, repo: DoctorsRepository | None = None) -> None:
        self._repo = repo or DoctorsRepository()

    def _scope_list(self, user: User, company_id_query: uuid.UUID | None) -> uuid.UUID:
        if user.role == UserRole.SUPER_ADMIN:
            if company_id_query is None:
                raise ValueError("company_id is required")
            return company_id_query
        return user.company_id

    def _scope_single(self, user: User) -> uuid.UUID | None:
        if user.role == UserRole.SUPER_ADMIN:
            return None
        return user.company_id

    async def _ensure_location_company(
        self, db: AsyncSession, location_id: uuid.UUID | None, expected_company_id: uuid.UUID
    ) -> None:
        if location_id is None:
            return
        cid = await self._repo.location_company_id(db, location_id)
        if cid is None:
            raise ValueError("Location not found")
        if cid != expected_company_id:
            raise ValueError("Location does not belong to this company")

    async def _validate_medical_stores(
        self, db: AsyncSession, company_id: uuid.UUID, store_ids: list[uuid.UUID]
    ) -> None:
        for sid in store_ids:
            sc = await self._repo.medical_store_company(db, sid)
            if sc is None:
                raise ValueError("Medical store not found")
            if sc != company_id:
                raise ValueError("Medical store wrong company")

    def _can_touch_doctor_company(self, user: User, doctor_company_id: uuid.UUID) -> bool:
        if user.role == UserRole.SUPER_ADMIN:
            return True
        return doctor_company_id == user.company_id

    def _doctor_company_id(self, user: User, body_company: uuid.UUID | None) -> uuid.UUID:
        if user.role == UserRole.SUPER_ADMIN:
            if body_company is None:
                raise ValueError("company_id is required for SUPER_ADMIN")
            return body_company
        if body_company is not None and body_company != user.company_id:
            raise ValueError("company_id must match your company")
        return user.company_id

    async def to_out(self, db: AsyncSession, doc: Doctor) -> DoctorOut:
        mids = await self._repo.list_medical_store_ids(db, doc.id)
        return DoctorOut(
            id=doc.id,
            company_id=doc.company_id,
            full_name=doc.full_name,
            specialization=doc.specialization,
            qualification=doc.qualification,
            phone=doc.phone,
            address=doc.address,
            location_id=doc.location_id,
            is_active=doc.is_active,
            medical_store_ids=mids,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )

    async def list_doctors(
        self,
        db: AsyncSession,
        user: User,
        *,
        company_id_query: uuid.UUID | None,
        q: str | None,
        location_id: uuid.UUID | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list[DoctorOut], int]:
        scope = self._scope_list(user, company_id_query)
        mr_filter = user.id if user.role == UserRole.MR else None
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_doctors(
            db,
            company_id=scope,
            q=q,
            location_id=location_id,
            active_only=not include_inactive,
            mr_id=mr_filter,
            limit=per_page,
            offset=offset,
        )
        outs: list[DoctorOut] = []
        for d in rows:
            outs.append(await self.to_out(db, d))
        return outs, total

    async def delete_doctor(self, db: AsyncSession, user: User, doctor_id: uuid.UUID) -> DoctorOut:
        row = await self._repo.get_doctor(db, doctor_id)
        if row is None:
            raise ValueError("Doctor not found")
        if not self._can_touch_doctor_company(user, row.company_id):
            raise PermissionError("Doctor not in your company")
        await self._repo.update_doctor(
            db,
            row,
            full_name=None,
            specialization=None,
            qualification=None,
            phone=None,
            address=None,
            location_id=None,
            is_active=False,
        )
        await db.refresh(row)
        return await self.to_out(db, row)

    async def get_doctor(self, db: AsyncSession, user: User, doctor_id: uuid.UUID) -> DoctorOut | None:
        scope = self._scope_single(user)
        row = await self._repo.get_doctor(db, doctor_id)
        if row is None:
            return None
        if scope is not None and row.company_id != scope:
            return None
        if user.role == UserRole.MR:
            visible, _ = await self._repo.list_doctors(
                db,
                company_id=row.company_id,
                active_only=True,
                mr_id=user.id,
                limit=5000,
                offset=0,
            )
            if doctor_id not in {d.id for d in visible}:
                return None
        return await self.to_out(db, row)

    async def create_doctor(self, db: AsyncSession, user: User, body: DoctorCreate) -> DoctorOut:
        company_id = self._doctor_company_id(user, body.company_id)
        await self._ensure_location_company(db, body.location_id, company_id)
        await self._validate_medical_stores(db, company_id, body.medical_store_ids)
        doc = await self._repo.create_doctor(
            db,
            company_id=company_id,
            full_name=body.full_name,
            specialization=body.specialization,
            qualification=body.qualification,
            phone=body.phone,
            address=body.address,
            location_id=body.location_id,
        )
        if body.medical_store_ids:
            await self._repo.set_medical_store_links(db, doc.id, body.medical_store_ids)
        return await self.to_out(db, doc)

    async def update_doctor(
        self, db: AsyncSession, user: User, doctor_id: uuid.UUID, body: DoctorUpdate
    ) -> DoctorOut:
        row = await self._repo.get_doctor(db, doctor_id)
        if row is None:
            raise ValueError("Doctor not found")
        if not self._can_touch_doctor_company(user, row.company_id):
            raise PermissionError("Doctor not in your company")
        data = body.model_dump(exclude_unset=True)
        store_ids = data.pop("medical_store_ids", None)
        loc = data.get("location_id")
        if loc is not None:
            await self._ensure_location_company(db, loc, row.company_id)
        if store_ids is not None:
            await self._validate_medical_stores(db, row.company_id, store_ids)
        if not data and store_ids is None:
            raise ValueError("No fields to update")
        if data:
            await self._repo.update_doctor(
                db,
                row,
                full_name=data.get("full_name"),
                specialization=data.get("specialization"),
                qualification=data.get("qualification"),
                phone=data.get("phone"),
                address=data.get("address"),
                location_id=data.get("location_id"),
                is_active=data.get("is_active"),
            )
        if store_ids is not None:
            await self._repo.set_medical_store_links(db, doctor_id, store_ids)
        await db.refresh(row)
        return await self.to_out(db, row)
