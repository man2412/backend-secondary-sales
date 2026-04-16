"""Add division_ids array to headquarters and drop division_id.

Revision ID: hq_multi_divisions
Revises: e923e536db94
Create Date: 2026-04-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "hq_multi_divisions"
down_revision: Union[str, None] = "e923e536db94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Add new division_ids column as UUID[]
    op.add_column(
        "headquarters",
        sa.Column(
            "division_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
    )

    # 2) Backfill division_ids from existing division_id
    op.execute(
        """
        UPDATE headquarters
        SET division_ids = ARRAY[division_id]::uuid[]
        WHERE division_id IS NOT NULL
        """
    )

    # 3) Make division_ids non-nullable
    op.alter_column("headquarters", "division_ids", nullable=False)

    # 4) Drop old division_id column and its FK
    op.drop_constraint("headquarters_division_id_fkey", "headquarters", type_="foreignkey")
    op.drop_column("headquarters", "division_id")


def downgrade() -> None:
    # 1) Recreate old division_id column
    op.add_column(
        "headquarters",
        sa.Column("division_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "headquarters_division_id_fkey",
        "headquarters",
        "divisions",
        ["division_id"],
        ["id"],
    )

    # 2) Backfill division_id from first element of division_ids
    op.execute(
        """
        UPDATE headquarters
        SET division_id = division_ids[1]
        WHERE division_ids IS NOT NULL AND array_length(division_ids, 1) >= 1
        """
    )

    # 3) Make division_id non-nullable
    op.alter_column("headquarters", "division_id", nullable=False)

    # 4) Drop division_ids column
    op.drop_column("headquarters", "division_ids")

