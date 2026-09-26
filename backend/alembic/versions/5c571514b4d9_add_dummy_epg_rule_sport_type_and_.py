"""add dummy_epg_rule sport_type and sport_fixture_cache

Revision ID: 5c571514b4d9
Revises: 031b56883865
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5c571514b4d9'
down_revision: Union[str, None] = '031b56883865'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SPORT_TYPE_ENUM = sa.Enum('rugby', 'nfl', native_enum=False, length=20, name='sporttype')


def upgrade() -> None:
    op.alter_column('dummy_epg_rules', 'pattern', existing_type=sa.Text(), nullable=True)
    op.add_column('dummy_epg_rules', sa.Column('sport_type', _SPORT_TYPE_ENUM, nullable=True))

    op.create_table(
        'sport_fixture_cache',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('sport_type', _SPORT_TYPE_ENUM, nullable=False),
        sa.Column('external_id', sa.String(length=64), nullable=False),
        sa.Column('competition', sa.String(length=255), nullable=False),
        sa.Column('kickoff', sa.DateTime(timezone=True), nullable=False),
        sa.Column('home', sa.String(length=255), nullable=False),
        sa.Column('away', sa.String(length=255), nullable=False),
        sa.Column('home_display', sa.String(length=255), nullable=False),
        sa.Column('away_display', sa.String(length=255), nullable=False),
        sa.Column('venue_name', sa.String(length=200), nullable=True),
        sa.Column('venue_city', sa.String(length=120), nullable=True),
        sa.Column('venue_state', sa.String(length=60), nullable=True),
        sa.UniqueConstraint('sport_type', 'external_id'),
    )
    op.create_index('ix_sport_fixture_cache_sport_type', 'sport_fixture_cache', ['sport_type'])
    op.create_index('ix_sport_fixture_cache_kickoff', 'sport_fixture_cache', ['kickoff'])


def downgrade() -> None:
    op.drop_index('ix_sport_fixture_cache_kickoff', table_name='sport_fixture_cache')
    op.drop_index('ix_sport_fixture_cache_sport_type', table_name='sport_fixture_cache')
    op.drop_table('sport_fixture_cache')

    op.drop_column('dummy_epg_rules', 'sport_type')
    op.execute("UPDATE dummy_epg_rules SET pattern = '' WHERE pattern IS NULL")
    op.alter_column('dummy_epg_rules', 'pattern', existing_type=sa.Text(), nullable=False)
