import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SuperStockist(Base, TimestampMixin):
    __tablename__ = "super_stockists"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unique_code: Mapped[str | None] = mapped_column(String(100))
    gst_number: Mapped[str | None] = mapped_column(String(20))
    drug_licence: Mapped[str | None] = mapped_column(String(100))
    pan: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text())
    location_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("locations.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SuperStockistContact(Base):
    __tablename__ = "super_stockist_contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    super_stockist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("super_stockists.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Stockist(Base, TimestampMixin):
    __tablename__ = "stockists"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    super_stockist_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("super_stockists.id")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unique_code: Mapped[str | None] = mapped_column(String(100))
    gst_number: Mapped[str | None] = mapped_column(String(20))
    drug_licence: Mapped[str | None] = mapped_column(String(100))
    pan: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text())
    location_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("locations.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StockistContact(Base):
    __tablename__ = "stockist_contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stockist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("stockists.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MedicalStore(Base, TimestampMixin):
    __tablename__ = "medical_stores"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stockist_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("stockists.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unique_code: Mapped[str | None] = mapped_column(String(100))
    gst_number: Mapped[str | None] = mapped_column(String(20))
    drug_licence: Mapped[str | None] = mapped_column(String(100))
    pan: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text())
    location_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("locations.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MedicalStoreContact(Base):
    __tablename__ = "medical_store_contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    medical_store_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("medical_stores.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
