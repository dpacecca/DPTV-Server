"""add playlist_category sport fields

Revision ID: b7f4a1c93d2e
Revises: e91dc4a711a4
Create Date: 2026-09-18 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7f4a1c93d2e'
down_revision: Union[str, None] = 'e91dc4a711a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'playlist_categories',
        sa.Column(
            'sport_type',
            sa.Enum('rugby', native_enum=False, length=20, name='sporttype'),
            nullable=True,
        ),
    )
    op.add_column('playlist_categories', sa.Column('sport_last_refreshed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('playlist_categories', sa.Column('sport_last_refresh_status', sa.String(length=20), nullable=True))
    op.add_column('playlist_categories', sa.Column('sport_last_refresh_error', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('playlist_categories', 'sport_last_refresh_error')
    op.drop_column('playlist_categories', 'sport_last_refresh_status')
    op.drop_column('playlist_categories', 'sport_last_refreshed_at')
    op.drop_column('playlist_categories', 'sport_type')
