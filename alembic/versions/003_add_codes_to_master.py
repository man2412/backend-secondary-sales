"""Add code fields to divisions/headquarters/locations.

Revision ID: 003_add_codes_to_master
Revises: 002_secondary_sales_is_active
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_add_codes_to_master"
down_revision: Union[str, None] = "002_secondary_sales_is_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("divisions", sa.Column("code", sa.String(length=50), nullable=True))
    op.add_column("headquarters", sa.Column("code", sa.String(length=50), nullable=True))
    op.add_column("locations", sa.Column("code", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("locations", "code")
    op.drop_column("headquarters", "code")
    op.drop_column("divisions", "code")

