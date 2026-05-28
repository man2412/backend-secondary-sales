"""link medical_stores, doctors and MR allocations to headquarters (drop location link)

Revision ID: 010_link_to_headquarters
Revises: 009_ss_mgr_snapshot
Create Date: 2026-05-23

Schema-only migration. Existing data in the renamed/replaced columns is
dropped (the application is on a clean dev DB; production deploys must
re-seed allocations after running this).

Changes:
  - medical_stores.location_id  -> medical_stores.headquarter_id (nullable, FK headquarters.id)
  - doctors.location_id         -> doctors.headquarter_id        (nullable, FK headquarters.id)
  - mr_location_allocations     -> mr_headquarter_allocations
      column location_id        -> headquarter_id
      drop unique(mr_id, location_id); add partial unique (mr_id, headquarter_id) WHERE is_active
  - secondary_sales.location_id -> nullable

The `locations` table itself is intentionally kept.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010_link_to_headquarters"
down_revision: Union[str, None] = "009_ss_mgr_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # medical_stores: drop location_id, add headquarter_id (nullable FK)
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE medical_stores DROP CONSTRAINT IF EXISTS medical_stores_location_id_fkey"
    )
    op.drop_column("medical_stores", "location_id")
    op.add_column(
        "medical_stores",
        sa.Column("headquarter_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_medical_stores_headquarter_id_headquarters",
        "medical_stores",
        "headquarters",
        ["headquarter_id"],
        ["id"],
    )
    op.create_index(
        "ix_medical_stores_headquarter_id",
        "medical_stores",
        ["headquarter_id"],
    )

    # ------------------------------------------------------------------
    # doctors: drop location_id, add headquarter_id (nullable FK)
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE doctors DROP CONSTRAINT IF EXISTS doctors_location_id_fkey"
    )
    op.drop_column("doctors", "location_id")
    op.add_column(
        "doctors",
        sa.Column("headquarter_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_doctors_headquarter_id_headquarters",
        "doctors",
        "headquarters",
        ["headquarter_id"],
        ["id"],
    )
    op.create_index(
        "ix_doctors_headquarter_id",
        "doctors",
        ["headquarter_id"],
    )

    # ------------------------------------------------------------------
    # mr_location_allocations -> mr_headquarter_allocations
    # Drop the old unique constraint and rename column + table.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE mr_location_allocations DROP CONSTRAINT IF EXISTS uq_mr_location"
    )
    op.execute(
        "ALTER TABLE mr_location_allocations DROP CONSTRAINT IF EXISTS mr_location_allocations_location_id_fkey"
    )
    # rename column then table to keep PK and other constraints intact
    op.alter_column(
        "mr_location_allocations",
        "location_id",
        new_column_name="headquarter_id",
    )
    op.rename_table("mr_location_allocations", "mr_headquarter_allocations")
    op.create_foreign_key(
        "fk_mr_headquarter_allocations_headquarter_id_headquarters",
        "mr_headquarter_allocations",
        "headquarters",
        ["headquarter_id"],
        ["id"],
    )
    # Partial unique index — active rows only
    op.create_index(
        "uq_mr_headquarter_active",
        "mr_headquarter_allocations",
        ["mr_id", "headquarter_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    # ------------------------------------------------------------------
    # secondary_sales.location_id: NOT NULL -> NULL
    # ------------------------------------------------------------------
    op.alter_column(
        "secondary_sales",
        "location_id",
        existing_type=sa.Uuid(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade for 010_link_to_headquarters is not supported; "
        "restore the database from backup if needed."
    )
