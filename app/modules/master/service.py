import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User
from app.modules.master.repository import MasterRepository
from app.modules.master.schemas import (
    DivisionCreate,
    DivisionUpdate,
    HeadquarterCreate,
    HeadquarterUpdate,
    LocationCreate,
    LocationUpdate,
    ProductCreate,
    ProductUpdate,
    StateCreate,
    StateUpdate,
)


class MasterService:
    def __init__(self, repo: MasterRepository | None = None) -> None:
        self._repo = repo or MasterRepository()

    def _ensure_super_admin(self, user: User) -> None:
        if user.role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only SUPER_ADMIN can modify master data")

    # --- State ---

    async def list_states(
        self,
        db: AsyncSession,
        user: User,
        *,
        q: str | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list, int]:
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_states(
            db,
            q=q,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def get_state(self, db: AsyncSession, user: User, state_id: uuid.UUID):
        row = await self._repo.get_state(db, state_id)
        if row is None:
            return None
        return row

    async def create_state(self, db: AsyncSession, user: User, body: StateCreate):
        self._ensure_super_admin(user)
        row = await self._repo.create_state(db, name=body.name, code=body.code)
        return row

    async def update_state(self, db: AsyncSession, user: User, state_id: uuid.UUID, body: StateUpdate):
        self._ensure_super_admin(user)
        row = await self._repo.get_state(db, state_id)
        if row is None:
            raise ValueError("State not found")
        data = body.model_dump(exclude_unset=True)
        if not data:
            raise ValueError("No fields to update")
        return await self._repo.update_state(
            db,
            row,
            name=data.get("name"),
            code=data.get("code"),
            is_active=data.get("is_active"),
        )

    # --- Division ---

    async def list_divisions(
        self,
        db: AsyncSession,
        user: User,
        *,
        q: str | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list, int]:
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_divisions(
            db,
            q=q,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def get_division(self, db: AsyncSession, user: User, division_id: uuid.UUID):
        row = await self._repo.get_division(db, division_id)
        if row is None:
            return None
        return row

    async def create_division(self, db: AsyncSession, user: User, body: DivisionCreate):
        self._ensure_super_admin(user)
        return await self._repo.create_division(db, name=body.name, code=body.code)

    async def update_division(self, db: AsyncSession, user: User, division_id: uuid.UUID, body: DivisionUpdate):
        self._ensure_super_admin(user)
        row = await self._repo.get_division(db, division_id)
        if row is None:
            raise ValueError("Division not found")
        data = body.model_dump(exclude_unset=True)
        if not data:
            raise ValueError("No fields to update")
        return await self._repo.update_division(
            db, row, name=data.get("name"), code=data.get("code"), is_active=data.get("is_active")
        )

    # --- Headquarter ---

    async def list_headquarters(
        self,
        db: AsyncSession,
        user: User,
        *,
        q: str | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list, int]:
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_headquarters(
            db,
            q=q,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def get_headquarter(self, db: AsyncSession, user: User, hq_id: uuid.UUID):
        row = await self._repo.get_headquarter(db, hq_id)
        if row is None:
            return None
        return row

    async def create_headquarter(self, db: AsyncSession, user: User, body: HeadquarterCreate):
        self._ensure_super_admin(user)
        st = await self._repo.get_state(db, body.state_id)
        div = await self._repo.get_division(db, body.division_id)
        if st is None or div is None:
            raise ValueError("State or division not found")
        return await self._repo.create_headquarter(
            db, state_id=body.state_id, division_id=body.division_id, name=body.name, code=body.code
        )

    async def update_headquarter(self, db: AsyncSession, user: User, hq_id: uuid.UUID, body: HeadquarterUpdate):
        self._ensure_super_admin(user)
        row = await self._repo.get_headquarter(db, hq_id)
        if row is None:
            raise ValueError("Headquarter not found")
        st = await self._repo.get_state(db, row.state_id)
        if st is None:
            raise ValueError("Headquarter not found")
        data = body.model_dump(exclude_unset=True)
        if not data:
            raise ValueError("No fields to update")
        return await self._repo.update_headquarter(
            db, row, name=data.get("name"), code=data.get("code"), is_active=data.get("is_active")
        )

    # --- Location ---

    async def list_locations(
        self,
        db: AsyncSession,
        user: User,
        *,
        q: str | None,
        headquarter_id: uuid.UUID | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list, int]:
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_locations(
            db,
            q=q,
            headquarter_id=headquarter_id,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def get_location(self, db: AsyncSession, user: User, location_id: uuid.UUID):
        row = await self._repo.get_location(db, location_id)
        if row is None:
            return None
        return row

    async def create_location(self, db: AsyncSession, user: User, body: LocationCreate):
        self._ensure_super_admin(user)
        hq = await self._repo.get_headquarter(db, body.headquarter_id)
        if hq is None:
            raise ValueError("Headquarter not found")
        return await self._repo.create_location(
            db, headquarter_id=body.headquarter_id, name=body.name, code=body.code
        )

    async def update_location(self, db: AsyncSession, user: User, location_id: uuid.UUID, body: LocationUpdate):
        self._ensure_super_admin(user)
        row = await self._repo.get_location(db, location_id)
        if row is None:
            raise ValueError("Location not found")
        hq = await self._repo.get_headquarter(db, row.headquarter_id)
        if hq is None:
            raise ValueError("Location not found")
        st = await self._repo.get_state(db, hq.state_id)
        if st is None:
            raise ValueError("Location not found")
        data = body.model_dump(exclude_unset=True)
        if not data:
            raise ValueError("No fields to update")
        return await self._repo.update_location(
            db, row, name=data.get("name"), code=data.get("code"), is_active=data.get("is_active")
        )

    # --- Product ---

    async def list_products(
        self,
        db: AsyncSession,
        user: User,
        *,
        q: str | None,
        division_id: uuid.UUID | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list, int]:
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_products(
            db,
            q=q,
            division_id=division_id,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def get_product(self, db: AsyncSession, user: User, product_id: uuid.UUID):
        row = await self._repo.get_product(db, product_id)
        if row is None:
            return None
        return row

    async def create_product(self, db: AsyncSession, user: User, body: ProductCreate):
        self._ensure_super_admin(user)
        div = await self._repo.get_division(db, body.division_id)
        if div is None:
            raise ValueError("Division not found")
        return await self._repo.create_product(
            db,
            division_id=body.division_id,
            name=body.name,
            pack_size=body.pack_size,
            mrp=body.mrp,
            ptr=body.ptr,
            pts=body.pts,
            hsn_code=body.hsn_code,
        )

    async def update_product(self, db: AsyncSession, user: User, product_id: uuid.UUID, body: ProductUpdate):
        self._ensure_super_admin(user)
        row = await self._repo.get_product(db, product_id)
        if row is None:
            raise ValueError("Product not found")
        div = await self._repo.get_division(db, row.division_id)
        if div is None:
            raise ValueError("Product not found")
        data = body.model_dump(exclude_unset=True)
        if not data:
            raise ValueError("No fields to update")
        return await self._repo.update_product(
            db,
            row,
            name=data.get("name"),
            pack_size=data.get("pack_size"),
            mrp=data.get("mrp"),
            ptr=data.get("ptr"),
            pts=data.get("pts"),
            hsn_code=data.get("hsn_code"),
            is_active=data.get("is_active"),
        )
