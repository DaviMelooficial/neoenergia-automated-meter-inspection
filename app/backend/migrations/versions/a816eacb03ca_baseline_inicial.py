"""baseline inicial

Revision ID: a816eacb03ca
Revises: 
Create Date: 2026-08-28 19:07:09.110026

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a816eacb03ca'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
