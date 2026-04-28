import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Computed

from app.models.base import Base, TimestampMixin


class SecondarySale(Base, TimestampMixin):
    __tablename__ = "secondary_sales"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mr_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    asm_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    rsm_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    state_head_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("products.id"), nullable=False)
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("doctors.id"))
    medical_store_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("medical_stores.id")
    )
    division_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("divisions.id"), nullable=False)
    headquarter_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("headquarters.id"), nullable=False
    )
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("locations.id"), nullable=False)
    state_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("states.id"), nullable=False)
    sale_date: Mapped[date] = mapped_column(Date, nullable=False)
    sale_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    free_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ptr: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    pts: Mapped[float | None] = mapped_column(Numeric(10, 2))
    mrp: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    special_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    total_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2),
        Computed("(sale_qty::numeric * COALESCE(special_price, ptr))", persisted=True),
    )
    reported_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    bill_ref: Mapped[str | None] = mapped_column(String(100))
    batch: Mapped[str | None] = mapped_column(String(100))
    pack: Mapped[str | None] = mapped_column(String(100))
    remarks: Mapped[str | None] = mapped_column(Text())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
