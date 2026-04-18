"""secondary_sales: add import fields (batch, bill_ref, pack, reported_amount); make pts nullable

Revision ID: 005_ss_import_fields
Revises: e923e536db94
Create Date: 2026-04-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "005_ss_import_fields"
down_revision: Union[str, None] = "hq_multi_divisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("secondary_sales", sa.Column("reported_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("secondary_sales", sa.Column("bill_ref", sa.String(100), nullable=True))
    op.add_column("secondary_sales", sa.Column("batch", sa.String(100), nullable=True))
    op.add_column("secondary_sales", sa.Column("pack", sa.String(100), nullable=True))
    op.alter_column("secondary_sales", "pts", nullable=True)


def downgrade() -> None:
    op.alter_column("secondary_sales", "pts", nullable=False)
    op.drop_column("secondary_sales", "pack")
    op.drop_column("secondary_sales", "batch")
    op.drop_column("secondary_sales", "bill_ref")
    op.drop_column("secondary_sales", "reported_amount")
