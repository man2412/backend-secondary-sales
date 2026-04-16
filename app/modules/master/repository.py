import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.master import Division, Headquarter, Location, Product, State


class MasterRepository:
    # --- State ---

    async def get_state(self, db: AsyncSession, state_id: uuid.UUID) -> State | None:
        r = await db.execute(select(State).where(State.id == state_id))
        return r.scalar_one_or_none()

    async def list_states(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[State], int]:
        base = select(State)
        count_q = select(func.count()).select_from(State)
        if active_only:
            base = base.where(State.is_active.is_(True))
            count_q = count_q.where(State.is_active.is_(True))
        if q is not None and q.strip():
            term = f"%{q.strip()}%"
            base = base.where((State.name.ilike(term)) | (State.code.ilike(term)))
            count_q = count_q.where((State.name.ilike(term)) | (State.code.ilike(term)))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(State.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_state(self, db: AsyncSession, *, name: str, code: str | None) -> State:
        row = State(name=name, code=code, is_active=True)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_state(self, db: AsyncSession, row: State, *, name: str | None, code: str | None, is_active: bool | None) -> State:
        if name is not None:
            row.name = name
        if code is not None:
            row.code = code
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row

    # --- Division ---

    async def get_division(self, db: AsyncSession, division_id: uuid.UUID) -> Division | None:
        r = await db.execute(select(Division).where(Division.id == division_id))
        return r.scalar_one_or_none()

    async def list_divisions(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Division], int]:
        base = select(Division)
        count_q = select(func.count()).select_from(Division)
        if active_only:
            base = base.where(Division.is_active.is_(True))
            count_q = count_q.where(Division.is_active.is_(True))
        if q is not None and q.strip():
            term = f"%{q.strip()}%"
            base = base.where(Division.name.ilike(term))
            count_q = count_q.where(Division.name.ilike(term))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(Division.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_division(
        self, db: AsyncSession, *, name: str, code: str | None
    ) -> Division:
        row = Division(name=name, code=code, is_active=True)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_division(
        self, db: AsyncSession, row: Division, *, name: str | None, code: str | None, is_active: bool | None
    ) -> Division:
        if name is not None:
            row.name = name
        if code is not None:
            row.code = code
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row

    # --- Headquarter ---

    async def get_headquarter(self, db: AsyncSession, hq_id: uuid.UUID) -> Headquarter | None:
        r = await db.execute(select(Headquarter).where(Headquarter.id == hq_id))
        return r.scalar_one_or_none()

    async def list_headquarters(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Headquarter], int]:
        base = select(Headquarter)
        count_q = select(func.count()).select_from(Headquarter)
        if active_only:
            base = base.where(Headquarter.is_active.is_(True))
            count_q = count_q.where(Headquarter.is_active.is_(True))
        if q is not None and q.strip():
            term = f"%{q.strip()}%"
            base = base.where(Headquarter.name.ilike(term))
            count_q = count_q.where(Headquarter.name.ilike(term))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(Headquarter.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_headquarter(
        self, db: AsyncSession, *, state_id: uuid.UUID, division_ids: list[uuid.UUID], name: str, code: str | None
    ) -> Headquarter:
        row = Headquarter(state_id=state_id, division_ids=division_ids, name=name, code=code, is_active=True)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_headquarter(
        self, db: AsyncSession, row: Headquarter, *, name: str | None, code: str | None, is_active: bool | None
    ) -> Headquarter:
        if name is not None:
            row.name = name
        if code is not None:
            row.code = code
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row

    # --- Location ---

    async def get_location(self, db: AsyncSession, location_id: uuid.UUID) -> Location | None:
        r = await db.execute(select(Location).where(Location.id == location_id))
        return r.scalar_one_or_none()

    async def list_locations(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        headquarter_id: uuid.UUID | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Location], int]:
        base = select(Location)
        count_q = select(func.count()).select_from(Location)
        if active_only:
            base = base.where(Location.is_active.is_(True))
            count_q = count_q.where(Location.is_active.is_(True))
        if headquarter_id is not None:
            base = base.where(Location.headquarter_id == headquarter_id)
            count_q = count_q.where(Location.headquarter_id == headquarter_id)
        if q is not None and q.strip():
            term = f"%{q.strip()}%"
            base = base.where(Location.name.ilike(term))
            count_q = count_q.where(Location.name.ilike(term))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(Location.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_location(
        self, db: AsyncSession, *, headquarter_id: uuid.UUID, name: str, code: str | None
    ) -> Location:
        row = Location(headquarter_id=headquarter_id, name=name, code=code, is_active=True)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_location(
        self, db: AsyncSession, row: Location, *, name: str | None, code: str | None, is_active: bool | None
    ) -> Location:
        if name is not None:
            row.name = name
        if code is not None:
            row.code = code
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row

    # --- Product ---

    async def get_product(self, db: AsyncSession, product_id: uuid.UUID) -> Product | None:
        r = await db.execute(select(Product).where(Product.id == product_id))
        return r.scalar_one_or_none()

    async def list_products(
        self,
        db: AsyncSession,
        *,
        q: str | None,
        division_id: uuid.UUID | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Product], int]:
        base = select(Product)
        count_q = select(func.count()).select_from(Product)
        if active_only:
            base = base.where(Product.is_active.is_(True))
            count_q = count_q.where(Product.is_active.is_(True))
        if division_id is not None:
            base = base.where(Product.division_id == division_id)
            count_q = count_q.where(Product.division_id == division_id)
        if q is not None and q.strip():
            term = f"%{q.strip()}%"
            base = base.where(Product.name.ilike(term))
            count_q = count_q.where(Product.name.ilike(term))
        total = (await db.execute(count_q)).scalar_one()
        base = base.order_by(Product.name).offset(offset).limit(limit)
        rows = (await db.execute(base)).scalars().all()
        return rows, int(total)

    async def create_product(
        self,
        db: AsyncSession,
        *,
        division_id: uuid.UUID,
        name: str,
        pack_size: str | None,
        mrp: float,
        ptr: float,
        pts: float,
        hsn_code: str | None,
    ) -> Product:
        row = Product(
            division_id=division_id,
            name=name,
            pack_size=pack_size,
            mrp=mrp,
            ptr=ptr,
            pts=pts,
            hsn_code=hsn_code,
            is_active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update_product(
        self,
        db: AsyncSession,
        row: Product,
        *,
        name: str | None,
        pack_size: str | None,
        mrp: float | None,
        ptr: float | None,
        pts: float | None,
        hsn_code: str | None,
        is_active: bool | None,
    ) -> Product:
        if name is not None:
            row.name = name
        if pack_size is not None:
            row.pack_size = pack_size
        if mrp is not None:
            row.mrp = mrp
        if ptr is not None:
            row.ptr = ptr
        if pts is not None:
            row.pts = pts
        if hsn_code is not None:
            row.hsn_code = hsn_code
        if is_active is not None:
            row.is_active = is_active
        await db.flush()
        await db.refresh(row)
        return row
