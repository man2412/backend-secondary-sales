"""create import_jobs table

Revision ID: 006_import_jobs
Revises: 005_ss_import_fields
Create Date: 2026-04-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "006_import_jobs"
down_revision: Union[str, None] = "005_ss_import_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE import_source_type AS ENUM ('pdf', 'xlsx', 'xls', 'csv')")
    op.execute("CREATE TYPE import_job_status AS ENUM ('processing', 'ready', 'committed', 'failed')")

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM("pdf", "xlsx", "xls", "csv", name="import_source_type", create_type=False),
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("mr_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("detected_fos_name", sa.String(255), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("processing", "ready", "committed", "failed", name="import_job_status", create_type=False),
            nullable=False,
            server_default="processing",
        ),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("llm_response", postgresql.JSONB, nullable=True),
        sa.Column("structured_rows", postgresql.JSONB, nullable=True),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("total_rows", sa.Integer, nullable=True),
        sa.Column("committed_count", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_import_jobs_uploaded_by", "import_jobs", ["uploaded_by"])
    op.create_index("ix_import_jobs_mr_id", "import_jobs", ["mr_id"])
    op.create_index("ix_import_jobs_file_hash", "import_jobs", ["file_hash"])
    op.create_index("ix_import_jobs_status", "import_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("import_jobs")
    op.execute("DROP TYPE IF EXISTS import_job_status")
    op.execute("DROP TYPE IF EXISTS import_source_type")
