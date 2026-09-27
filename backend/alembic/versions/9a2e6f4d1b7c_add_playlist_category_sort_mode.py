"""add playlist_category sort_mode

Revision ID: 9a2e6f4d1b7c
Revises: 5c571514b4d9
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9a2e6f4d1b7c'
down_revision: Union[str, None] = '5c571514b4d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SORT_MODE_ENUM = sa.Enum('manual', 'event_state', native_enum=False, length=20, name='categorysortmode')


def upgrade() -> None:
    op.add_column(
        'playlist_categories',
        sa.Column('sort_mode', _SORT_MODE_ENUM, nullable=False, server_default='manual'),
    )


def downgrade() -> None:
    op.drop_column('playlist_categories', 'sort_mode')
