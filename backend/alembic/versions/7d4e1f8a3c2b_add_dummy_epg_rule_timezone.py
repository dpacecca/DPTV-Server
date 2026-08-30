"""add dummy epg rule timezone

Revision ID: 7d4e1f8a3c2b
Revises: f3a7c2e91b6d
Create Date: 2026-08-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7d4e1f8a3c2b'
down_revision: Union[str, None] = 'f3a7c2e91b6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('dummy_epg_rules', sa.Column('timezone', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('dummy_epg_rules', 'timezone')
