"""Add is_active to secondary_sales for soft delete.

Revision ID: 002_secondary_sales_is_active
Revises: 001_initial
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_secondary_sales_is_active"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "secondary_sales",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.alter_column("secondary_sales", "is_active", server_default=None)


def downgrade() -> None:
    op.drop_column("secondary_sales", "is_active")
