"""
Bulk DB helpers for the entity-import flow.

The orchestrator (`service.py`) calls these in batches — one query per
phase rather than one per row. That keeps the per-row work in Python
(grouping / fuzzy matching) and the DB busy only with the wide reads and
the final writes.

Naming convention:
    list_*  → SELECT many rows
    upsert_*→ idempotent INSERT (no-op on existing match)
    bulk_*  → multi-row INSERT … ON CONFLICT
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrDoctorAllocation
from app.models.doctor import Doctor, DoctorMedicalStore
from app.models.enums import UserRole
from app.models.master import Division, Headquarter, State
from app.models.stockist import MedicalStore, Stockist
from app.models.user import User


# ---------------------------------------------------------------------------
# Stockists
# ---------------------------------------------------------------------------


class EntityImportRepository:
    async def list_active_stockists(self, db: AsyncSession) -> Sequence[Stockist]:
        r = await db.execute(select(Stockist).where(Stockist.is_active.is_(True)))
        return r.scalars().all()

    async def insert_stockist(self, db: AsyncSession, *, name: str) -> Stockist:
        row = Stockist(name=name, is_active=True)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    # ---- Headquarters -----------------------------------------------------

    async def list_headquarters_by_names(
        self, db: AsyncSession, names: Iterable[str]
    ) -> Sequence[Headquarter]:
        names_clean = [n for n in names if n]
        if not names_clean:
            return []
        # case-insensitive match
        r = await db.execute(
            select(Headquarter).where(func.lower(Headquarter.name).in_([n.lower() for n in names_clean]))
        )
        return r.scalars().all()

    async def get_state(self, db: AsyncSession, state_id: uuid.UUID) -> State | None:
        r = await db.execute(select(State).where(State.id == state_id))
        return r.scalar_one_or_none()

    async def list_divisions_by_ids(
        self, db: AsyncSession, division_ids: Iterable[uuid.UUID]
    ) -> Sequence[Division]:
        ids = [d for d in division_ids if d is not None]
        if not ids:
            return []
        r = await db.execute(select(Division).where(Division.id.in_(ids)))
        return r.scalars().all()

    async def insert_headquarter(
        self,
        db: AsyncSession,
        *,
        name: str,
        state_id: uuid.UUID,
        division_ids: list[uuid.UUID],
    ) -> Headquarter:
        row = Headquarter(
            name=name,
            state_id=state_id,
            division_ids=list(division_ids),
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    # ---- Medical stores ---------------------------------------------------

    async def list_stores_for_stockists(
        self, db: AsyncSession, stockist_ids: Iterable[uuid.UUID]
    ) -> Sequence[MedicalStore]:
        ids = [i for i in stockist_ids if i is not None]
        if not ids:
            return []
        r = await db.execute(
            select(MedicalStore).where(MedicalStore.stockist_id.in_(ids))
        )
        return r.scalars().all()

    async def insert_medical_store(
        self,
        db: AsyncSession,
        *,
        name: str,
        stockist_id: uuid.UUID | None,
        headquarter_id: uuid.UUID | None,
        alternate_names: list[str],
        address: str | None,
    ) -> MedicalStore:
        row = MedicalStore(
            name=name,
            stockist_id=stockist_id,
            headquarter_id=headquarter_id,
            alternate_names=alternate_names or None,
            address=address,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_store_alternates(
        self,
        db: AsyncSession,
        store: MedicalStore,
        *,
        new_alternates: list[str] | None,
        new_name: str | None = None,
    ) -> None:
        changed = False
        if new_alternates is not None:
            existing = list(store.alternate_names or [])
            merged = list({*existing, *new_alternates})
            if set(merged) != set(existing):
                store.alternate_names = merged
                changed = True
        if new_name and new_name != store.name:
            # only adopt a new primary name if the old one was empty
            if not store.name:
                store.name = new_name
                changed = True
        if changed:
            await db.flush()

    # ---- Doctors ----------------------------------------------------------

    async def list_doctors_by_hq(
        self, db: AsyncSession, hq_ids: Iterable[uuid.UUID]
    ) -> Sequence[Doctor]:
        ids = [i for i in hq_ids if i is not None]
        if not ids:
            return []
        r = await db.execute(
            select(Doctor).where(Doctor.headquarter_id.in_(ids))
        )
        return r.scalars().all()

    async def list_doctors_with_null_hq(self, db: AsyncSession) -> Sequence[Doctor]:
        """Doctors that haven't been assigned an HQ yet — also candidates for matching."""
        r = await db.execute(select(Doctor).where(Doctor.headquarter_id.is_(None)))
        return r.scalars().all()

    async def insert_doctor(
        self,
        db: AsyncSession,
        *,
        full_name: str,
        headquarter_id: uuid.UUID | None,
        phone: str | None = None,
        address: str | None = None,
        specialization: str | None = None,
        qualification: str | None = None,
    ) -> Doctor:
        row = Doctor(
            full_name=full_name,
            headquarter_id=headquarter_id,
            phone=phone,
            address=address,
            specialization=specialization,
            qualification=qualification,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_doctor_hq(
        self, db: AsyncSession, doctor: Doctor, hq_id: uuid.UUID
    ) -> None:
        if doctor.headquarter_id != hq_id:
            doctor.headquarter_id = hq_id
            await db.flush()

    # ---- Doctor ↔ MedicalStore links --------------------------------------

    async def list_existing_doctor_store_links(
        self,
        db: AsyncSession,
        doctor_ids: Iterable[uuid.UUID],
    ) -> set[tuple[uuid.UUID, uuid.UUID]]:
        ids = [d for d in doctor_ids if d is not None]
        if not ids:
            return set()
        r = await db.execute(
            select(DoctorMedicalStore.doctor_id, DoctorMedicalStore.medical_store_id).where(
                DoctorMedicalStore.doctor_id.in_(ids)
            )
        )
        return {(row[0], row[1]) for row in r.all()}

    async def bulk_insert_doctor_store_links(
        self,
        db: AsyncSession,
        pairs: Iterable[tuple[uuid.UUID, uuid.UUID]],
    ) -> int:
        """
        Insert (doctor_id, medical_store_id) pairs idempotently. The table's
        composite primary key gives us natural ON CONFLICT DO NOTHING.
        Returns the number of inserted rows.
        """
        values = [
            {"doctor_id": d, "medical_store_id": s}
            for d, s in pairs
            if d is not None and s is not None
        ]
        if not values:
            return 0
        stmt = (
            pg_insert(DoctorMedicalStore.__table__)
            .values(values)
            .on_conflict_do_nothing(index_elements=["doctor_id", "medical_store_id"])
        )
        result = await db.execute(stmt)
        return int(result.rowcount or 0)

    # ---- Users (MR resolution) -------------------------------------------

    async def list_mr_users(self, db: AsyncSession) -> Sequence[User]:
        r = await db.execute(
            select(User).where(User.role == UserRole.MR, User.is_active.is_(True))
        )
        return r.scalars().all()

    # ---- MR ↔ Doctor allocations -----------------------------------------

    async def list_existing_mr_doctor_pairs(
        self,
        db: AsyncSession,
        mr_ids: Iterable[uuid.UUID],
    ) -> dict[tuple[uuid.UUID, uuid.UUID], MrDoctorAllocation]:
        ids = [m for m in mr_ids if m is not None]
        if not ids:
            return {}
        r = await db.execute(
            select(MrDoctorAllocation).where(MrDoctorAllocation.mr_id.in_(ids))
        )
        out: dict[tuple[uuid.UUID, uuid.UUID], MrDoctorAllocation] = {}
        for row in r.scalars().all():
            out[(row.mr_id, row.doctor_id)] = row
        return out

    async def bulk_upsert_mr_doctor_allocations(
        self,
        db: AsyncSession,
        *,
        rows: list[dict],
    ) -> int:
        """
        Insert (mr_id, doctor_id, allocated_by) tuples; if a row already exists
        for (mr_id, doctor_id), refresh `is_active=true` and `allocated_by`.
        Returns the number of affected rows (inserted + updated).
        """
        if not rows:
            return 0
        stmt = pg_insert(MrDoctorAllocation.__table__).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_mr_doctor",
            set_={
                "is_active": True,
                "allocated_by": stmt.excluded.allocated_by,
            },
        )
        result = await db.execute(stmt)
        return int(result.rowcount or 0)
