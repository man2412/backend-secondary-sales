import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User
from app.modules.allocations.repository import AllocationsRepository
from app.modules.allocations.schemas import (
    AllocationsBundleOut,
    AllocationOps,
    DoctorAllocCreate,
    DoctorAllocOut,
    LocationAllocCreate,
    LocationAllocOut,
    ProductAllocCreate,
    ProductAllocOut,
    StoreAllocCreate,
    StoreAllocOut,
)
from app.modules.doctors.repository import DoctorsRepository
from app.modules.master.repository import MasterRepository
from app.modules.stockists.repository import StockistsRepository
from app.modules.users.repository import UserRepository
from app.modules.users.service import UserService

# Create/remove MR allocations: any role above MR (not MR).
_ALLOCATION_MANAGER_ROLES: frozenset[UserRole] = frozenset(
    {
        UserRole.SUPER_ADMIN,
        UserRole.SALES_DIRECTOR,
        UserRole.STATE_HEAD,
        UserRole.RSM,
        UserRole.DEPUTY_RSM,
        UserRole.ASM,
    }
)


class AllocationsService:
    def __init__(self, repo: AllocationsRepository | None = None) -> None:
        self._repo = repo or AllocationsRepository()
        self._users = UserRepository()
        self._master = MasterRepository()
        self._stockists = StockistsRepository()
        self._doctors = DoctorsRepository()

    def _require_can_manage_allocations(self, user: User) -> None:
        if user.role not in _ALLOCATION_MANAGER_ROLES:
            raise PermissionError(
                "Only management roles can create or remove allocations (not MR)"
            )

    async def _require_view_mr(self, db: AsyncSession, user: User, mr_id: uuid.UUID) -> User:
        visible = await UserService().get_visible_mr_ids(db, user)
        if mr_id not in visible:
            raise PermissionError("Cannot view allocations for this MR")
        target = await self._users.get_by_id(db, mr_id)
        if target is None or target.role != UserRole.MR or not target.is_active:
            raise ValueError("Target is not an active MR")
        return target

    async def get_bundle(
        self, db: AsyncSession, user: User, mr_id: uuid.UUID, *, include_inactive: bool
    ) -> AllocationsBundleOut:
        await self._require_view_mr(db, user, mr_id)
        active_only = not include_inactive
        locs = await self._repo.list_location_alloc_rows(db, mr_id, active_only)
        docs = await self._repo.list_doctor_alloc_rows(db, mr_id, active_only)
        stores = await self._repo.list_store_alloc_rows(db, mr_id, active_only)
        prods = await self._repo.list_product_alloc_rows(db, mr_id, active_only)

        loc_out: list[LocationAllocOut] = []
        for a in locs:
            loc_out.append(
                LocationAllocOut(
                    id=a.id,
                    mr_id=a.mr_id,
                    location_id=a.location_id,
                    location_name=await self._repo.location_name(db, a.location_id),
                    allocated_by=a.allocated_by,
                    allocated_at=a.allocated_at,
                    is_active=a.is_active,
                )
            )
        doc_out: list[DoctorAllocOut] = []
        for a in docs:
            doc_out.append(
                DoctorAllocOut(
                    id=a.id,
                    mr_id=a.mr_id,
                    doctor_id=a.doctor_id,
                    doctor_name=await self._repo.doctor_name(db, a.doctor_id),
                    division_id=a.division_id,
                    division_name=await self._repo.division_name(db, a.division_id),
                    allocated_by=a.allocated_by,
                    allocated_at=a.allocated_at,
                    is_active=a.is_active,
                )
            )
        st_out: list[StoreAllocOut] = []
        for a in stores:
            st_out.append(
                StoreAllocOut(
                    id=a.id,
                    mr_id=a.mr_id,
                    medical_store_id=a.medical_store_id,
                    store_name=await self._repo.store_name(db, a.medical_store_id),
                    allocated_by=a.allocated_by,
                    allocated_at=a.allocated_at,
                    is_active=a.is_active,
                )
            )
        pr_out: list[ProductAllocOut] = []
        for a in prods:
            pr_out.append(
                ProductAllocOut(
                    id=a.id,
                    mr_id=a.mr_id,
                    product_id=a.product_id,
                    product_name=await self._repo.product_name(db, a.product_id),
                    allocated_by=a.allocated_by,
                    allocated_at=a.allocated_at,
                    is_active=a.is_active,
                )
            )
        return AllocationsBundleOut(
            locations=loc_out, doctors=doc_out, medical_stores=st_out, products=pr_out
        )

    async def add_location(
        self, db: AsyncSession, user: User, mr_id: uuid.UUID, body: LocationAllocCreate
    ) -> LocationAllocOut:
        self._require_can_manage_allocations(user)
        await self._require_view_mr(db, user, mr_id)
        loc = await self._master.get_location(db, body.location_id)
        if loc is None or not loc.is_active:
            raise ValueError("Location not found")
        row = await self._repo.upsert_location_alloc(
            db, mr_id=mr_id, location_id=body.location_id, allocated_by=user.id
        )
        return LocationAllocOut(
            id=row.id,
            mr_id=row.mr_id,
            location_id=row.location_id,
            location_name=await self._repo.location_name(db, row.location_id),
            allocated_by=row.allocated_by,
            allocated_at=row.allocated_at,
            is_active=row.is_active,
        )

    async def add_doctor(
        self, db: AsyncSession, user: User, mr_id: uuid.UUID, body: DoctorAllocCreate
    ) -> DoctorAllocOut:
        self._require_can_manage_allocations(user)
        await self._require_view_mr(db, user, mr_id)
        doc = await self._doctors.get_doctor(db, body.doctor_id)
        if doc is None or not doc.is_active:
            raise ValueError("Doctor not found")
        div = await self._master.get_division(db, body.division_id)
        if div is None or not div.is_active:
            raise ValueError("Division not found")
        row = await self._repo.upsert_doctor_alloc(
            db,
            mr_id=mr_id,
            doctor_id=body.doctor_id,
            division_id=body.division_id,
            allocated_by=user.id,
        )
        return DoctorAllocOut(
            id=row.id,
            mr_id=row.mr_id,
            doctor_id=row.doctor_id,
            doctor_name=await self._repo.doctor_name(db, row.doctor_id),
            division_id=row.division_id,
            division_name=await self._repo.division_name(db, row.division_id),
            allocated_by=row.allocated_by,
            allocated_at=row.allocated_at,
            is_active=row.is_active,
        )

    async def add_product(
        self, db: AsyncSession, user: User, mr_id: uuid.UUID, body: ProductAllocCreate
    ) -> ProductAllocOut:
        self._require_can_manage_allocations(user)
        await self._require_view_mr(db, user, mr_id)
        pr = await self._master.get_product(db, body.product_id)
        if pr is None or not pr.is_active:
            raise ValueError("Product not found")
        row = await self._repo.upsert_product_alloc(
            db, mr_id=mr_id, product_id=body.product_id, allocated_by=user.id
        )
        return ProductAllocOut(
            id=row.id,
            mr_id=row.mr_id,
            product_id=row.product_id,
            product_name=await self._repo.product_name(db, row.product_id),
            allocated_by=row.allocated_by,
            allocated_at=row.allocated_at,
            is_active=row.is_active,
        )

    async def add_store(self, db: AsyncSession, user: User, mr_id: uuid.UUID, body: StoreAllocCreate) -> StoreAllocOut:
        self._require_can_manage_allocations(user)
        await self._require_view_mr(db, user, mr_id)
        st = await self._stockists.get_medical_store(db, body.medical_store_id)
        if st is None or not st.is_active:
            raise ValueError("Medical store not found")
        row = await self._repo.upsert_store_alloc(
            db, mr_id=mr_id, medical_store_id=body.medical_store_id, allocated_by=user.id
        )
        return StoreAllocOut(
            id=row.id,
            mr_id=row.mr_id,
            medical_store_id=row.medical_store_id,
            store_name=await self._repo.store_name(db, row.medical_store_id),
            allocated_by=row.allocated_by,
            allocated_at=row.allocated_at,
            is_active=row.is_active,
        )

    async def delete_location(self, db: AsyncSession, user: User, alloc_id: uuid.UUID) -> None:
        self._require_can_manage_allocations(user)
        row = await self._repo.get_location_alloc(db, alloc_id)
        if row is None:
            raise ValueError("Allocation not found")
        await self._require_view_mr(db, user, row.mr_id)
        await self._repo.soft_delete_location(db, row)

    async def delete_doctor(self, db: AsyncSession, user: User, alloc_id: uuid.UUID) -> None:
        self._require_can_manage_allocations(user)
        row = await self._repo.get_doctor_alloc(db, alloc_id)
        if row is None:
            raise ValueError("Allocation not found")
        await self._require_view_mr(db, user, row.mr_id)
        await self._repo.soft_delete_doctor(db, row)

    async def delete_product(self, db: AsyncSession, user: User, alloc_id: uuid.UUID) -> None:
        self._require_can_manage_allocations(user)
        row = await self._repo.get_product_alloc(db, alloc_id)
        if row is None:
            raise ValueError("Allocation not found")
        await self._require_view_mr(db, user, row.mr_id)
        await self._repo.soft_delete_product(db, row)

    async def delete_store(self, db: AsyncSession, user: User, alloc_id: uuid.UUID) -> None:
        self._require_can_manage_allocations(user)
        row = await self._repo.get_store_alloc(db, alloc_id)
        if row is None:
            raise ValueError("Allocation not found")
        await self._require_view_mr(db, user, row.mr_id)
        await self._repo.soft_delete_store(db, row)

    async def apply_ops(self, db: AsyncSession, user: User, mr_id: uuid.UUID, ops: AllocationOps) -> AllocationsBundleOut:
        """Single endpoint to add/remove allocations for an MR."""
        self._require_can_manage_allocations(user)
        await self._require_view_mr(db, user, mr_id)

        for lid in ops.add_locations:
            await self.add_location(db, user, mr_id, LocationAllocCreate(location_id=lid))
        for did in ops.remove_location_alloc_ids:
            await self.delete_location(db, user, did)

        for d in ops.add_doctors:
            await self.add_doctor(db, user, mr_id, d)
        for did in ops.remove_doctor_alloc_ids:
            await self.delete_doctor(db, user, did)

        for sid in ops.add_stores:
            await self.add_store(db, user, mr_id, StoreAllocCreate(medical_store_id=sid))
        for sid in ops.remove_store_alloc_ids:
            await self.delete_store(db, user, sid)

        for pid in ops.add_products:
            await self.add_product(db, user, mr_id, ProductAllocCreate(product_id=pid))
        for pid in ops.remove_product_alloc_ids:
            await self.delete_product(db, user, pid)

        return await self.get_bundle(db, user, mr_id, include_inactive=True)
