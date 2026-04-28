import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrDoctorAllocation, MrLocationAllocation, MrStoreAllocation
from app.models.enums import UserRole
from app.models.sale import SecondarySale
from app.models.user import User
from app.modules.doctors.repository import DoctorsRepository
from app.modules.master.repository import MasterRepository
from app.modules.sales.repository import SalesRepository
from app.modules.sales.schemas import SecondarySaleCreate, SecondarySaleUpdate
from app.modules.stockists.repository import StockistsRepository
from app.modules.users.repository import UserRepository
from app.modules.users.service import UserService


MAX_SALES_PER_MR_PER_DAY = 100


def _f(x: Decimal | float) -> float:
    return float(x)


def _special_price_for_db(value: float | None) -> float | None:
    """`total_amount` is `sale_qty * COALESCE(special_price, ptr)`. A sent value of 0 is non-NULL in SQL, so COALESCE picks 0 and wipes revenue; treat 0 as “use PTR” (store NULL)."""
    if value is None or value == 0:
        return None
    return value


async def resolve_manager_chain(
    db: AsyncSession, mr_id: uuid.UUID
) -> dict[str, uuid.UUID | None]:
    """
    Walk the reports_to chain upward starting from `mr_id` and snapshot the first
    user encountered for each manager-level role.

    Returns a dict with keys: asm_id, rsm_id, state_head_id (any may be None).

    The snapshot is stored on SecondarySale rows so that historical sales remain
    visible to the manager who owned that MR at the time of the sale, even after
    the MR is reassigned to a different manager.
    """
    q = text(
        """
        WITH RECURSIVE chain AS (
            SELECT id, role, reports_to, 0 AS depth
            FROM users WHERE id = :uid
            UNION ALL
            SELECT u.id, u.role, u.reports_to, c.depth + 1
            FROM users u
            INNER JOIN chain c ON c.reports_to = u.id
        )
        SELECT id, role FROM chain ORDER BY depth
        """
    )
    rows = (await db.execute(q, {"uid": str(mr_id)})).fetchall()

    asm_id: uuid.UUID | None = None
    rsm_id: uuid.UUID | None = None
    state_head_id: uuid.UUID | None = None

    for row in rows:
        uid_val, role_val = row[0], row[1]
        role_str = role_val.value if hasattr(role_val, "value") else str(role_val)

        if asm_id is None and role_str == UserRole.ASM.value:
            asm_id = uuid.UUID(str(uid_val))
        elif rsm_id is None and role_str in (
            UserRole.RSM.value,
            UserRole.DEPUTY_RSM.value,
        ):
            rsm_id = uuid.UUID(str(uid_val))
        elif state_head_id is None and role_str == UserRole.STATE_HEAD.value:
            state_head_id = uuid.UUID(str(uid_val))

        if asm_id is not None and rsm_id is not None and state_head_id is not None:
            break

    return {"asm_id": asm_id, "rsm_id": rsm_id, "state_head_id": state_head_id}


class SalesService:
    def __init__(self, repo: SalesRepository | None = None) -> None:
        self._repo = repo or SalesRepository()
        self._master = MasterRepository()
        self._doctors = DoctorsRepository()
        self._stockists = StockistsRepository()

    async def _has_doctor_alloc(
        self, db: AsyncSession, mr_id: uuid.UUID, doctor_id: uuid.UUID
    ) -> bool:
        r = await db.execute(
            select(MrDoctorAllocation.id).where(
                MrDoctorAllocation.mr_id == mr_id,
                MrDoctorAllocation.doctor_id == doctor_id,
                MrDoctorAllocation.is_active.is_(True),
            )
        )
        return r.one_or_none() is not None

    async def _has_store_alloc(self, db: AsyncSession, mr_id: uuid.UUID, store_id: uuid.UUID) -> bool:
        r = await db.execute(
            select(MrStoreAllocation.id).where(
                MrStoreAllocation.mr_id == mr_id,
                MrStoreAllocation.medical_store_id == store_id,
                MrStoreAllocation.is_active.is_(True),
            )
        )
        return r.one_or_none() is not None

    async def _has_location_alloc(
        self, db: AsyncSession, mr_id: uuid.UUID, location_id: uuid.UUID
    ) -> bool:
        r = await db.execute(
            select(MrLocationAllocation.id).where(
                MrLocationAllocation.mr_id == mr_id,
                MrLocationAllocation.location_id == location_id,
                MrLocationAllocation.is_active.is_(True),
            )
        )
        return r.one_or_none() is not None

    def _to_out(self, row: SecondarySale) -> dict:
        return {
            "id": row.id,
            "mr_id": row.mr_id,
            "asm_id": row.asm_id,
            "rsm_id": row.rsm_id,
            "state_head_id": row.state_head_id,
            "product_id": row.product_id,
            "doctor_id": row.doctor_id,
            "medical_store_id": row.medical_store_id,
            "division_id": row.division_id,
            "headquarter_id": row.headquarter_id,
            "location_id": row.location_id,
            "state_id": row.state_id,
            "sale_date": row.sale_date,
            "sale_qty": row.sale_qty,
            "free_qty": row.free_qty,
            "ptr": _f(row.ptr),
            "pts": _f(row.pts) if row.pts is not None else None,
            "mrp": _f(row.mrp),
            "special_price": float(row.special_price) if row.special_price is not None else None,
            "total_amount": float(row.total_amount) if row.total_amount is not None else None,
            "reported_amount": float(row.reported_amount) if row.reported_amount is not None else None,
            "bill_ref": row.bill_ref,
            "batch": row.batch,
            "pack": row.pack,
            "remarks": row.remarks,
            "is_active": row.is_active,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def list_sales(
        self,
        db: AsyncSession,
        user: User,
        *,
        page: int,
        per_page: int,
        sale_date: date | None,
        mr_id_filter: uuid.UUID | None,
        include_inactive: bool,
    ) -> tuple[list[dict], int]:
        # `visible` is still used to validate the optional mr_id_filter and as
        # the fallback set for legacy rows whose snapshot columns are NULL.
        visible = await UserService().get_visible_mr_ids(db, user)
        if mr_id_filter is not None and user.role not in (
            UserRole.SUPER_ADMIN,
            UserRole.SALES_DIRECTOR,
        ) and mr_id_filter not in visible:
            raise PermissionError("Cannot list sales for this MR")
        # MR shortcut: their visible set is just themselves; if empty (inactive),
        # there's nothing to return.
        if user.role == UserRole.MR and not visible:
            return [], 0
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_sales(
            db,
            caller_id=user.id,
            caller_role=user.role,
            fallback_mr_ids=list(visible),
            mr_id_filter=mr_id_filter,
            sale_date=sale_date,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return [self._to_out(r) for r in rows], total

    async def get_sale(
        self,
        db: AsyncSession,
        user: User,
        sale_id: uuid.UUID,
    ) -> dict | None:
        row = await self._repo.get_sale(db, sale_id)
        if row is None:
            return None

        if user.role in (UserRole.SUPER_ADMIN, UserRole.SALES_DIRECTOR):
            return self._to_out(row)
        if user.role == UserRole.MR:
            return self._to_out(row) if row.mr_id == user.id else None

        # Manager roles: prefer snapshot match; fall back to live subtree for
        # rows persisted before snapshot columns existed.
        if user.role == UserRole.ASM:
            snapshot_id = row.asm_id
        elif user.role in (UserRole.RSM, UserRole.DEPUTY_RSM):
            snapshot_id = row.rsm_id
        elif user.role == UserRole.STATE_HEAD:
            snapshot_id = row.state_head_id
        else:
            return None

        if snapshot_id == user.id:
            return self._to_out(row)
        if snapshot_id is None:
            visible = await UserService().get_visible_mr_ids(db, user)
            if row.mr_id in visible:
                return self._to_out(row)
        return None

    async def create_sale(self, db: AsyncSession, user: User, body: SecondarySaleCreate) -> dict:
        if user.role == UserRole.MR:
            mr_id = user.id
        elif user.role == UserRole.SUPER_ADMIN:
            if body.mr_id is None:
                raise ValueError("mr_id is required when creating a sale as SUPER_ADMIN")
            mr_id = body.mr_id
        else:
            raise PermissionError("Only MR or SUPER_ADMIN can create secondary sales")
        if body.doctor_id is None and body.medical_store_id is None:
            raise ValueError("Provide doctor_id and/or medical_store_id")
        urepo = UserRepository()
        mr_user = await urepo.get_by_id(db, mr_id)
        if mr_user is None or mr_user.role != UserRole.MR or not mr_user.is_active:
            raise ValueError("Target mr_id must be an active MR user")
        ctx = await self._repo.get_location_context(db, body.location_id)
        if ctx is None:
            raise ValueError("Location not found")
        state_id, hq_id, division_id = ctx
        loc_row = await self._master.get_location(db, body.location_id)
        if loc_row is None or not loc_row.is_active:
            raise ValueError("Location not found")
        product = await self._master.get_product(db, body.product_id)
        if product is None or not product.is_active:
            raise ValueError("Product not found")
        if product.division_id != division_id:
            raise ValueError("Product division does not match location division")
        div = await self._master.get_division(db, division_id)
        if div is None:
            raise ValueError("Invalid division for sale")
        if not await self._has_location_alloc(db, mr_id, body.location_id):
            raise ValueError("Location not allocated to you")
        if body.doctor_id is not None:
            doc = await self._doctors.get_doctor(db, body.doctor_id)
            if doc is None or not doc.is_active:
                raise ValueError("Doctor not found")
            if not await self._has_doctor_alloc(db, mr_id, body.doctor_id):
                raise ValueError("Doctor not allocated to you")
        if body.medical_store_id is not None:
            st = await self._stockists.get_medical_store(db, body.medical_store_id)
            if st is None or not st.is_active:
                raise ValueError("Medical store not found")
            if not await self._has_store_alloc(db, mr_id, body.medical_store_id):
                raise ValueError("Medical store not allocated to you")
        n = await self._repo.count_mr_sales_on_date(db, mr_id=mr_id, sale_date=body.sale_date)
        if n >= MAX_SALES_PER_MR_PER_DAY:
            raise ValueError(f"Daily limit of {MAX_SALES_PER_MR_PER_DAY} active sales reached for this date")
        chain = await resolve_manager_chain(db, mr_id)
        row = await self._repo.create_sale(
            db,
            mr_id=mr_id,
            asm_id=chain["asm_id"],
            rsm_id=chain["rsm_id"],
            state_head_id=chain["state_head_id"],
            product_id=body.product_id,
            doctor_id=body.doctor_id,
            medical_store_id=body.medical_store_id,
            division_id=division_id,
            headquarter_id=hq_id,
            location_id=body.location_id,
            state_id=state_id,
            sale_date=body.sale_date,
            sale_qty=body.sale_qty,
            free_qty=body.free_qty,
            ptr=_f(product.ptr),
            pts=_f(product.pts),
            mrp=_f(product.mrp),
            special_price=_special_price_for_db(body.special_price),
            remarks=body.remarks,
        )
        return self._to_out(row)

    async def update_sale(
        self, db: AsyncSession, user: User, sale_id: uuid.UUID, body: SecondarySaleUpdate
    ) -> dict:
        if user.role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only SUPER_ADMIN can update secondary sales")
        row = await self._repo.get_sale(db, sale_id)
        if row is None:
            raise ValueError("Sale not found")
        data = body.model_dump(exclude_unset=True)
        if not data:
            raise ValueError("No fields to update")
        if "sale_qty" in data and data["sale_qty"] < 1:
            raise ValueError("sale_qty must be at least 1")
        if "special_price" in data:
            data["special_price"] = _special_price_for_db(data["special_price"])
        await self._repo.update_sale(db, row, data)
        return self._to_out(row)

    async def delete_sale(self, db: AsyncSession, user: User, sale_id: uuid.UUID) -> None:
        if user.role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only SUPER_ADMIN can remove secondary sales")
        row = await self._repo.get_sale(db, sale_id)
        if row is None:
            raise ValueError("Sale not found")
        await self._repo.soft_delete_sale(db, row)
