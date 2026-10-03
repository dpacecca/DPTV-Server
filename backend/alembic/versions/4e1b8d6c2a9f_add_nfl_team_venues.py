"""add nfl_team_venues

Revision ID: 4e1b8d6c2a9f
Revises: 9a2e6f4d1b7c
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4e1b8d6c2a9f'
down_revision: Union[str, None] = '9a2e6f4d1b7c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'nfl_team_venues',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('team_id', sa.String(length=32), nullable=False),
        sa.Column('team_name', sa.String(length=255), nullable=True),
        sa.Column('venue_name', sa.String(length=200), nullable=True),
        sa.Column('venue_city', sa.String(length=120), nullable=True),
        sa.Column('venue_state', sa.String(length=60), nullable=True),
    )
    op.create_index('ix_nfl_team_venues_team_id', 'nfl_team_venues', ['team_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_nfl_team_venues_team_id', table_name='nfl_team_venues')
    op.drop_table('nfl_team_venues')
