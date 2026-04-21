import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ImportJobStatus(str, enum.Enum):
    processing = "processing"
    ready = "ready"
    partial = "partial"
    committed = "committed"
    failed = "failed"


class ImportSourceType(str, enum.Enum):
    pdf = "pdf"
    xlsx = "xlsx"
    xls = "xls"
    csv = "csv"


class ImportJob(Base, TimestampMixin):
    __tablename__ = "import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[ImportSourceType] = mapped_column(
        Enum(ImportSourceType, name="import_source_type"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    mr_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    detected_fos_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[ImportJobStatus] = mapped_column(
        Enum(ImportJobStatus, name="import_job_status"),
        nullable=False,
        default=ImportJobStatus.processing,
    )
    raw_text: Mapped[str | None] = mapped_column(Text)
    llm_response: Mapped[dict | None] = mapped_column(JSONB)
    structured_rows: Mapped[list | None] = mapped_column(JSONB)
    model_used: Mapped[str | None] = mapped_column(String(100))
    total_rows: Mapped[int | None] = mapped_column(Integer)
    committed_count: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    chunks_total: Mapped[int | None] = mapped_column(Integer)
    chunks_succeeded: Mapped[int | None] = mapped_column(Integer)
    extraction_warnings: Mapped[list | None] = mapped_column(JSONB)
