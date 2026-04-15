"""remove company_id single tenant

Revision ID: e923e536db94
Revises: 003_add_codes_to_master
Create Date: 2026-04-15 19:42:10.685569

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e923e536db94'
down_revision: Union[str, None] = '003_add_codes_to_master'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop FKs that reference `companies` then remove columns and table.
    # Constraint names are the default PostgreSQL style: "<table>_<column>_fkey".
    op.execute("ALTER TABLE IF EXISTS divisions DROP CONSTRAINT IF EXISTS divisions_company_id_fkey")
    op.execute("ALTER TABLE IF EXISTS states DROP CONSTRAINT IF EXISTS states_company_id_fkey")
    op.execute("ALTER TABLE IF EXISTS super_stockists DROP CONSTRAINT IF EXISTS super_stockists_company_id_fkey")
    op.execute("ALTER TABLE IF EXISTS stockists DROP CONSTRAINT IF EXISTS stockists_company_id_fkey")
    op.execute("ALTER TABLE IF EXISTS medical_stores DROP CONSTRAINT IF EXISTS medical_stores_company_id_fkey")
    op.execute("ALTER TABLE IF EXISTS doctors DROP CONSTRAINT IF EXISTS doctors_company_id_fkey")
    op.execute("ALTER TABLE IF EXISTS users DROP CONSTRAINT IF EXISTS users_company_id_fkey")
    op.execute("ALTER TABLE IF EXISTS secondary_sales DROP CONSTRAINT IF EXISTS secondary_sales_company_id_fkey")

    with op.batch_alter_table("divisions") as b:
        b.drop_column("company_id")
    with op.batch_alter_table("states") as b:
        b.drop_column("company_id")
    with op.batch_alter_table("super_stockists") as b:
        b.drop_column("company_id")
    with op.batch_alter_table("stockists") as b:
        b.drop_column("company_id")
    with op.batch_alter_table("medical_stores") as b:
        b.drop_column("company_id")
    with op.batch_alter_table("doctors") as b:
        b.drop_column("company_id")
    with op.batch_alter_table("users") as b:
        b.drop_column("company_id")
    with op.batch_alter_table("secondary_sales") as b:
        b.drop_column("company_id")

    op.drop_table("companies")


def downgrade() -> None:
    # Best-effort downgrade: restore `companies` table and nullable company_id columns.
    # Data cannot be reconstructed once removed.
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    with op.batch_alter_table("divisions") as b:
        b.add_column(sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True))
        b.create_foreign_key("divisions_company_id_fkey", "companies", ["company_id"], ["id"], ondelete="CASCADE")
    with op.batch_alter_table("states") as b:
        b.add_column(sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True))
        b.create_foreign_key("states_company_id_fkey", "companies", ["company_id"], ["id"], ondelete="CASCADE")
    with op.batch_alter_table("super_stockists") as b:
        b.add_column(sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True))
        b.create_foreign_key("super_stockists_company_id_fkey", "companies", ["company_id"], ["id"])
    with op.batch_alter_table("stockists") as b:
        b.add_column(sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True))
        b.create_foreign_key("stockists_company_id_fkey", "companies", ["company_id"], ["id"])
    with op.batch_alter_table("medical_stores") as b:
        b.add_column(sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True))
        b.create_foreign_key("medical_stores_company_id_fkey", "companies", ["company_id"], ["id"])
    with op.batch_alter_table("doctors") as b:
        b.add_column(sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True))
        b.create_foreign_key("doctors_company_id_fkey", "companies", ["company_id"], ["id"])
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True))
        b.create_foreign_key("users_company_id_fkey", "companies", ["company_id"], ["id"])
    with op.batch_alter_table("secondary_sales") as b:
        b.add_column(sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True))
        b.create_foreign_key("secondary_sales_company_id_fkey", "companies", ["company_id"], ["id"])
