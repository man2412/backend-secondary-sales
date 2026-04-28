"""stockist location cleanup, medical store alternate names, product net_rate, allocation changes

Revision ID: 008_schema_cleanup
Revises: 007_import_chunks
Create Date: 2026-04-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_schema_cleanup"
down_revision: Union[str, None] = "007_import_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM mr_doctor_allocations d
        WHERE EXISTS (
            SELECT 1 FROM mr_doctor_allocations d2
            WHERE d2.mr_id = d.mr_id AND d2.doctor_id = d.doctor_id
            AND d2.ctid < d.ctid
        )
        """
    )

    op.drop_constraint("uq_mr_doctor_div", "mr_doctor_allocations", type_="unique")
    op.execute(
        "ALTER TABLE mr_doctor_allocations DROP CONSTRAINT IF EXISTS "
        "mr_doctor_allocations_division_id_fkey"
    )
    op.drop_column("mr_doctor_allocations", "division_id")
    op.create_unique_constraint("uq_mr_doctor", "mr_doctor_allocations", ["mr_id", "doctor_id"])

    op.drop_table("mr_product_allocations")

    op.execute(
        "ALTER TABLE super_stockists DROP CONSTRAINT IF EXISTS super_stockists_location_id_fkey"
    )
    op.drop_column("super_stockists", "location_id")

    op.execute("ALTER TABLE stockists DROP CONSTRAINT IF EXISTS stockists_location_id_fkey")
    op.drop_column("stockists", "location_id")

    op.add_column(
        "medical_stores",
        sa.Column("alternate_names", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column("products", sa.Column("net_rate", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade for 008_schema_cleanup is not supported; restore the database from backup if needed."
    )
