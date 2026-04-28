"""secondary_sales: snapshot manager chain (asm_id, rsm_id, state_head_id)

Revision ID: 009_ss_mgr_snapshot
Revises: 008_schema_cleanup
Create Date: 2026-04-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "009_ss_mgr_snapshot"
down_revision: Union[str, None] = "008_schema_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "secondary_sales",
        sa.Column("asm_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "secondary_sales",
        sa.Column("rsm_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "secondary_sales",
        sa.Column("state_head_id", sa.Uuid(as_uuid=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_secondary_sales_asm_id_users",
        "secondary_sales",
        "users",
        ["asm_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_secondary_sales_rsm_id_users",
        "secondary_sales",
        "users",
        ["rsm_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_secondary_sales_state_head_id_users",
        "secondary_sales",
        "users",
        ["state_head_id"],
        ["id"],
    )

    # Indexes for the snapshot RBAC list filter
    op.create_index(
        "ix_secondary_sales_asm_id",
        "secondary_sales",
        ["asm_id"],
    )
    op.create_index(
        "ix_secondary_sales_rsm_id",
        "secondary_sales",
        ["rsm_id"],
    )
    op.create_index(
        "ix_secondary_sales_state_head_id",
        "secondary_sales",
        ["state_head_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_secondary_sales_state_head_id", table_name="secondary_sales")
    op.drop_index("ix_secondary_sales_rsm_id", table_name="secondary_sales")
    op.drop_index("ix_secondary_sales_asm_id", table_name="secondary_sales")

    op.drop_constraint(
        "fk_secondary_sales_state_head_id_users", "secondary_sales", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_secondary_sales_rsm_id_users", "secondary_sales", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_secondary_sales_asm_id_users", "secondary_sales", type_="foreignkey"
    )

    op.drop_column("secondary_sales", "state_head_id")
    op.drop_column("secondary_sales", "rsm_id")
    op.drop_column("secondary_sales", "asm_id")
