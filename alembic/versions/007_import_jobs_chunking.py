"""add chunking fields + partial status to import_jobs

Revision ID: 007_import_chunks
Revises: 006_import_jobs
Create Date: 2026-04-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "007_import_chunks"
down_revision: Union[str, None] = "006_import_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 'partial' to enum
    op.execute("ALTER TYPE import_job_status ADD VALUE IF NOT EXISTS 'partial'")

    op.add_column(
        "import_jobs",
        sa.Column("chunks_total", sa.Integer, nullable=True),
    )
    op.add_column(
        "import_jobs",
        sa.Column("chunks_succeeded", sa.Integer, nullable=True),
    )
    op.add_column(
        "import_jobs",
        sa.Column("extraction_warnings", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_jobs", "extraction_warnings")
    op.drop_column("import_jobs", "chunks_succeeded")
    op.drop_column("import_jobs", "chunks_total")
    # Enum values can't be removed in postgres without recreating the type; leave 'partial' in place.
