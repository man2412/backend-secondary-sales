"""Create all tables from SQLAlchemy metadata.

Revision ID: 001_initial
Revises:
Create Date: 2025-03-21

"""

from typing import Sequence, Union

from alembic import op

from app.models import Base

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
