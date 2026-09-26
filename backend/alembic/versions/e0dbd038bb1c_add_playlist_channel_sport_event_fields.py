"""add playlist_channel sport_event fields

Revision ID: e0dbd038bb1c
Revises: c4f8b2e71a9d
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e0dbd038bb1c'
down_revision: Union[str, None] = 'c4f8b2e71a9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('playlist_channels', sa.Column('sport_event_title', sa.String(length=300), nullable=True))
    op.add_column('playlist_channels', sa.Column('sport_event_start', sa.DateTime(timezone=True), nullable=True))
    op.add_column('playlist_channels', sa.Column('sport_event_venue_name', sa.String(length=200), nullable=True))
    op.add_column('playlist_channels', sa.Column('sport_event_venue_city', sa.String(length=120), nullable=True))
    op.add_column('playlist_channels', sa.Column('sport_event_venue_state', sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column('playlist_channels', 'sport_event_venue_state')
    op.drop_column('playlist_channels', 'sport_event_venue_city')
    op.drop_column('playlist_channels', 'sport_event_venue_name')
    op.drop_column('playlist_channels', 'sport_event_start')
    op.drop_column('playlist_channels', 'sport_event_title')
