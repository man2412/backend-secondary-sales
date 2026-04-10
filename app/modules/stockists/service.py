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

    def _ensure_super_admin(self, user: User) -> None:
        if user.role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only SUPER_ADMIN can modify this resource")

    async def list_super_stockists(
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
    ) -> tuple[list, int]:
        scope = self._scope_list(user, company_id_query)
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_super_stockists(
            db,
            company_id=scope,
            q=q,
            location_id=location_id,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def get_super_stockist(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        scope = self._scope_single(user)
        row = await self._repo.get_super_stockist(db, entity_id)
        if row is None:
            return None
        if scope is not None and row.company_id != scope:
            return None
        return row

    async def create_super_stockist(self, db: AsyncSession, user: User, body: SuperStockistCreate):
        self._ensure_super_admin(user)
        await self._ensure_location_company(db, body.location_id, body.company_id)
        return await self._repo.create_super_stockist(
            db,
            company_id=body.company_id,
            name=body.name,
            unique_code=body.unique_code,
            gst_number=body.gst_number,
            drug_licence=body.drug_licence,
            pan=body.pan,
            address=body.address,
            location_id=body.location_id,
        )

    async def update_super_stockist(
        self, db: AsyncSession, user: User, entity_id: uuid.UUID, body: SuperStockistUpdate
    ):
        self._ensure_super_admin(user)
        row = await self._repo.get_super_stockist(db, entity_id)
        if row is None:
            raise ValueError("Super stockist not found")
        scope = self._scope_single(user)
        if scope is not None and row.company_id != scope:
            raise ValueError("Super stockist not found")
        data = body.model_dump(exclude_unset=True)
        loc = data.get("location_id")
        if loc is not None:
            await self._ensure_location_company(db, loc, row.company_id)
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
            location_id=data.get("location_id"),
            is_active=data.get("is_active"),
        )

    async def list_stockists(
        self,
        db: AsyncSession,
        user: User,
        *,
        company_id_query: uuid.UUID | None,
        q: str | None,
        super_stockist_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list, int]:
        scope = self._scope_list(user, company_id_query)
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_stockists(
            db,
            company_id=scope,
            q=q,
            super_stockist_id=super_stockist_id,
            location_id=location_id,
            active_only=not include_inactive,
            limit=per_page,
            offset=offset,
        )
        return list(rows), total

    async def get_stockist(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        scope = self._scope_single(user)
        row = await self._repo.get_stockist(db, entity_id)
        if row is None:
            return None
        if scope is not None and row.company_id != scope:
            return None
        return row

    async def create_stockist(self, db: AsyncSession, user: User, body: StockistCreate):
        self._ensure_super_admin(user)
        await self._ensure_location_company(db, body.location_id, body.company_id)
        if body.super_stockist_id is not None:
            ss = await self._repo.get_super_stockist(db, body.super_stockist_id)
            if ss is None or ss.company_id != body.company_id:
                raise ValueError("Super stockist not found or wrong company")
        return await self._repo.create_stockist(
            db,
            company_id=body.company_id,
            super_stockist_id=body.super_stockist_id,
            name=body.name,
            unique_code=body.unique_code,
            gst_number=body.gst_number,
            drug_licence=body.drug_licence,
            pan=body.pan,
            address=body.address,
            location_id=body.location_id,
        )

    async def update_stockist(self, db: AsyncSession, user: User, entity_id: uuid.UUID, body: StockistUpdate):
        self._ensure_super_admin(user)
        row = await self._repo.get_stockist(db, entity_id)
        if row is None:
            raise ValueError("Stockist not found")
        scope = self._scope_single(user)
        if scope is not None and row.company_id != scope:
            raise ValueError("Stockist not found")
        data = body.model_dump(exclude_unset=True)
        if "super_stockist_id" in data and data["super_stockist_id"] is not None:
            ss = await self._repo.get_super_stockist(db, data["super_stockist_id"])
            if ss is None or ss.company_id != row.company_id:
                raise ValueError("Super stockist not found or wrong company")
        loc = data.get("location_id")
        if loc is not None:
            await self._ensure_location_company(db, loc, row.company_id)
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
            location_id=data.get("location_id"),
            is_active=data.get("is_active"),
        )

    def _medical_company_id(self, user: User, body_company: uuid.UUID | None) -> uuid.UUID:
        if user.role == UserRole.SUPER_ADMIN:
            if body_company is None:
                raise ValueError("company_id is required for SUPER_ADMIN")
            return body_company
        if body_company is not None and body_company != user.company_id:
            raise ValueError("company_id must match your company")
        return user.company_id

    def _can_touch_medical_store(self, user: User, row_company_id: uuid.UUID) -> bool:
        if user.role == UserRole.SUPER_ADMIN:
            return True
        return row_company_id == user.company_id

    async def list_medical_stores(
        self,
        db: AsyncSession,
        user: User,
        *,
        company_id_query: uuid.UUID | None,
        q: str | None,
        stockist_id: uuid.UUID | None,
        location_id: uuid.UUID | None,
        page: int,
        per_page: int,
        include_inactive: bool,
    ) -> tuple[list, int]:
        scope = self._scope_list(user, company_id_query)
        mr_filter = user.id if user.role == UserRole.MR else None
        offset = (page - 1) * per_page
        rows, total = await self._repo.list_medical_stores(
            db,
            company_id=scope,
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
        scope = self._scope_single(user)
        if scope is not None and row.company_id != scope:
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
            location_id=None,
            is_active=False,
        )

    async def delete_stockist(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        self._ensure_super_admin(user)
        row = await self._repo.get_stockist(db, entity_id)
        if row is None:
            raise ValueError("Stockist not found")
        scope = self._scope_single(user)
        if scope is not None and row.company_id != scope:
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
            location_id=None,
            is_active=False,
        )

    async def delete_medical_store(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        row = await self._repo.get_medical_store(db, entity_id)
        if row is None:
            raise ValueError("Medical store not found")
        if not self._can_touch_medical_store(user, row.company_id):
            raise PermissionError("Medical store not in your company")
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
            is_active=False,
        )

    async def get_medical_store(self, db: AsyncSession, user: User, entity_id: uuid.UUID):
        scope = self._scope_single(user)
        row = await self._repo.get_medical_store(db, entity_id)
        if row is None:
            return None
        if scope is not None and row.company_id != scope:
            return None
        if user.role == UserRole.MR:
            rows, _ = await self._repo.list_medical_stores(
                db,
                company_id=row.company_id,
                active_only=True,
                mr_id=user.id,
                limit=5000,
                offset=0,
            )
            if entity_id not in {r.id for r in rows}:
                return None
        return row

    async def create_medical_store(self, db: AsyncSession, user: User, body: MedicalStoreCreate):
        company_id = self._medical_company_id(user, body.company_id)
        await self._ensure_location_company(db, body.location_id, company_id)
        if body.stockist_id is not None:
            st = await self._repo.get_stockist(db, body.stockist_id)
            if st is None or st.company_id != company_id:
                raise ValueError("Stockist not found or wrong company")
        return await self._repo.create_medical_store(
            db,
            company_id=company_id,
            stockist_id=body.stockist_id,
            name=body.name,
            unique_code=body.unique_code,
            gst_number=body.gst_number,
            drug_licence=body.drug_licence,
            pan=body.pan,
            address=body.address,
            location_id=body.location_id,
        )

    async def update_medical_store(
        self, db: AsyncSession, user: User, entity_id: uuid.UUID, body: MedicalStoreUpdate
    ):
        row = await self._repo.get_medical_store(db, entity_id)
        if row is None:
            raise ValueError("Medical store not found")
        if not self._can_touch_medical_store(user, row.company_id):
            raise PermissionError("Medical store not in your company")
        data = body.model_dump(exclude_unset=True)
        if "stockist_id" in data and data["stockist_id"] is not None:
            st = await self._repo.get_stockist(db, data["stockist_id"])
            if st is None or st.company_id != row.company_id:
                raise ValueError("Stockist not found or wrong company")
        loc = data.get("location_id")
        if loc is not None:
            await self._ensure_location_company(db, loc, row.company_id)
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
            is_active=data.get("is_active"),
        )
