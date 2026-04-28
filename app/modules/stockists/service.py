import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole
from app.models.user import User
from app.modules.stockists.repository import StockistsRepository
from app.modules.stockists.schemas import (
    MedicalStoreCreate,
    MedicalStoreUpdate,
    StockistCreate,
    StockistUpdate,
    SuperStockistCreate,
    SuperStockistUpdate,
)


class StockistsService:
    def __init__(self, repo: StockistsRepository | None = None) -> None:
        self._repo = repo or StockistsRepository()

    def _ensure_super_admin(self, user: User) -> None:
        if user.role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only SUPER_ADMIN can modify this resource")

    async def list_super_stockists(
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
        rows, total = await self._repo.list_super_stockists(
            db,
            q=q,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def get_super_stockist(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        row = await self._repo.get_super_stockist(db, entity_id)
        if row is None:
            return None
        return row

    async def create_super_stockist(self, db: AsyncSession, user: User, body: SuperStockistCreate):
        self._ensure_super_admin(user)
        return await self._repo.create_super_stockist(
            db,
            name=body.name,
            unique_code=body.unique_code,
            gst_number=body.gst_number,
            drug_licence=body.drug_licence,
            pan=body.pan,
            address=body.address,
        )

    async def update_super_stockist(
        self, db: AsyncSession, user: User, entity_id: uuid.UUID, body: SuperStockistUpdate
    ):
        self._ensure_super_admin(user)
        row = await self._repo.get_super_stockist(db, entity_id)
        if row is None:
            raise ValueError("Super stockist not found")
        data = body.model_dump(exclude_unset=True)
        if not data:
            raise ValueError("No fields to update")
        return await self._repo.update_super_stockist(
            db,
            row,
            name=data.get("name"),
            unique_code=data.get("unique_code"),
            gst_number=data.get("gst_number"),
            drug_licence=data.get("drug_licence"),
            pan=data.get("pan"),
            address=data.get("address"),
            is_active=data.get("is_active"),
        )

    async def list_stockists(
        self,
        db: AsyncSession,
        user: User,
        *,
        q: str | None,
        super_stockist_id: uuid.UUID | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list, int]:
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_stockists(
            db,
            q=q,
            super_stockist_id=super_stockist_id,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def get_stockist(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        row = await self._repo.get_stockist(db, entity_id)
        if row is None:
            return None
        return row

    async def create_stockist(self, db: AsyncSession, user: User, body: StockistCreate):
        self._ensure_super_admin(user)
        if body.super_stockist_id is not None:
            ss = await self._repo.get_super_stockist(db, body.super_stockist_id)
            if ss is None:
                raise ValueError("Super stockist not found")
        return await self._repo.create_stockist(
            db,
            super_stockist_id=body.super_stockist_id,
            name=body.name,
            unique_code=body.unique_code,
            gst_number=body.gst_number,
            drug_licence=body.drug_licence,
            pan=body.pan,
            address=body.address,
        )

    async def update_stockist(self, db: AsyncSession, user: User, entity_id: uuid.UUID, body: StockistUpdate):
        self._ensure_super_admin(user)
        row = await self._repo.get_stockist(db, entity_id)
        if row is None:
            raise ValueError("Stockist not found")
        data = body.model_dump(exclude_unset=True)
        if "super_stockist_id" in data and data["super_stockist_id"] is not None:
            ss = await self._repo.get_super_stockist(db, data["super_stockist_id"])
            if ss is None:
                raise ValueError("Super stockist not found")
        if not data:
            raise ValueError("No fields to update")
        return await self._repo.update_stockist(
            db,
            row,
            super_stockist_id=data.get("super_stockist_id"),
            name=data.get("name"),
            unique_code=data.get("unique_code"),
            gst_number=data.get("gst_number"),
            drug_licence=data.get("drug_licence"),
            pan=data.get("pan"),
            address=data.get("address"),
            is_active=data.get("is_active"),
        )

    async def list_medical_stores(
        self,
        db: AsyncSession,
        user: User,
        *,
        q: str | None,
        stockist_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list, int]:
        mr_filter = user.id if user.role == UserRole.MR else None
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_medical_stores(
            db,
            q=q,
            stockist_id=stockist_id,
            location_id=location_id,
            active_only=not include_inactive,
            mr_id=mr_filter,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def delete_super_stockist(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        self._ensure_super_admin(user)
        row = await self._repo.get_super_stockist(db, entity_id)
        if row is None:
            raise ValueError("Super stockist not found")
        return await self._repo.update_super_stockist(
            db,
            row,
            name=None,
            unique_code=None,
            gst_number=None,
            drug_licence=None,
            pan=None,
            address=None,
            is_active=False,
        )

    async def delete_stockist(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        self._ensure_super_admin(user)
        row = await self._repo.get_stockist(db, entity_id)
        if row is None:
            raise ValueError("Stockist not found")
        return await self._repo.update_stockist(
            db,
            row,
            super_stockist_id=None,
            name=None,
            unique_code=None,
            gst_number=None,
            drug_licence=None,
            pan=None,
            address=None,
            is_active=False,
        )

    async def delete_medical_store(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        row = await self._repo.get_medical_store(db, entity_id)
        if row is None:
            raise ValueError("Medical store not found")
        return await self._repo.update_medical_store(
            db,
            row,
            stockist_id=None,
            name=None,
            unique_code=None,
            gst_number=None,
            drug_licence=None,
            pan=None,
            address=None,
            location_id=None,
            alternate_names=[],
            is_active=False,
        )

    async def get_medical_store(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        row = await self._repo.get_medical_store(db, entity_id)
        if row is None:
            return None
        if user.role == UserRole.MR:
            rows, _ = await self._repo.list_medical_stores(
                db,
                q=None,
                stockist_id=None,
                location_id=None,
                active_only=True,
                mr_id=user.id,
                limit=5000,
                offset=0,
            )
            if entity_id not in {r.id for r in rows}:
                return None
        return row

    async def create_medical_store(self, db: AsyncSession, user: User, body: MedicalStoreCreate):
        if body.stockist_id is not None:
            st = await self._repo.get_stockist(db, body.stockist_id)
            if st is None:
                raise ValueError("Stockist not found")
        return await self._repo.create_medical_store(
            db,
            stockist_id=body.stockist_id,
            name=body.name,
            unique_code=body.unique_code,
            gst_number=body.gst_number,
            drug_licence=body.drug_licence,
            pan=body.pan,
            address=body.address,
            location_id=body.location_id,
            alternate_names=body.alternate_names,
        )

    async def update_medical_store(
        self, db: AsyncSession, user: User, entity_id: uuid.UUID, body: MedicalStoreUpdate
    ):
        row = await self._repo.get_medical_store(db, entity_id)
        if row is None:
            raise ValueError("Medical store not found")
        data = body.model_dump(exclude_unset=True)
        if "stockist_id" in data and data["stockist_id"] is not None:
            st = await self._repo.get_stockist(db, data["stockist_id"])
            if st is None:
                raise ValueError("Stockist not found")
        if not data:
            raise ValueError("No fields to update")
        return await self._repo.update_medical_store(
            db,
            row,
            stockist_id=data.get("stockist_id"),
            name=data.get("name"),
            unique_code=data.get("unique_code"),
            gst_number=data.get("gst_number"),
            drug_licence=data.get("drug_licence"),
            pan=data.get("pan"),
            address=data.get("address"),
            location_id=data.get("location_id"),
            alternate_names=data.get("alternate_names"),
            is_active=data.get("is_active"),
        )
