"""secondary_sales: sale_qty / free_qty Integer -> Numeric (fractional quantities)

Revision ID: 011_fractional_quantities
Revises: 010_link_to_headquarters
Create Date: 2026-06-11

Distributors sell fractional units (e.g. 2.5 strips, 0.5 free). The columns were
INTEGER, so imports floored 2.5 -> 2 and totals / total_amount drifted from the
source files. This widens both quantity columns to NUMERIC(12,2).

`total_amount` is a STORED generated column derived from `sale_qty`, so Postgres
won't let us alter `sale_qty`'s type while it exists — we drop it, change the
types, then recreate it with the SAME expression. It's generated, so no data is
lost (it recomputes from the existing rows).

Backward compatible: existing whole-number rows convert losslessly (5 -> 5.00).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011_fractional_quantities"
down_revision: Union[str, None] = "010_link_to_headquarters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_GEN_EXPR = "((sale_qty)::numeric * COALESCE(special_price, ptr))"


def upgrade() -> None:
    op.execute("ALTER TABLE secondary_sales DROP COLUMN total_amount")
    op.alter_column(
        "secondary_sales", "sale_qty",
        existing_type=sa.Integer(), type_=sa.Numeric(12, 2),
        existing_nullable=False, postgresql_using="sale_qty::numeric(12,2)",
    )
    op.alter_column(
        "secondary_sales", "free_qty",
        existing_type=sa.Integer(), type_=sa.Numeric(12, 2),
        existing_nullable=False, postgresql_using="free_qty::numeric(12,2)",
    )
    op.execute(
        "ALTER TABLE secondary_sales ADD COLUMN total_amount numeric(12,2) "
        f"GENERATED ALWAYS AS {_GEN_EXPR} STORED"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE secondary_sales DROP COLUMN total_amount")
    op.alter_column(
        "secondary_sales", "free_qty",
        existing_type=sa.Numeric(12, 2), type_=sa.Integer(),
        existing_nullable=False, postgresql_using="round(free_qty)::integer",
    )
    op.alter_column(
        "secondary_sales", "sale_qty",
        existing_type=sa.Numeric(12, 2), type_=sa.Integer(),
        existing_nullable=False, postgresql_using="round(sale_qty)::integer",
    )
    op.execute(
        "ALTER TABLE secondary_sales ADD COLUMN total_amount numeric(12,2) "
        f"GENERATED ALWAYS AS {_GEN_EXPR} STORED"
    )
