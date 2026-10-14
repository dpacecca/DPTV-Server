"""remove live sport fixture scraping and category auto-sort

Revision ID: d3d6935be030
Revises: 8c3f5a2e7d1b
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3d6935be030'
down_revision: Union[str, None] = '8c3f5a2e7d1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SPORT_TYPE_ENUM = sa.Enum('rugby', 'nfl', native_enum=False, length=20, name='sporttype')
_SORT_MODE_ENUM = sa.Enum('manual', 'event_state', native_enum=False, length=20, name='categorysortmode')


def upgrade() -> None:
    # A sport-type rule's pattern is always NULL - delete rather than backfill, same reasoning
    # as a Live Sport category below (nothing left to compute what it'd even match).
    op.execute("DELETE FROM dummy_epg_rules WHERE sport_type IS NOT NULL")
    op.drop_column('dummy_epg_rules', 'sport_type')
    op.execute("UPDATE dummy_epg_rules SET pattern = '' WHERE pattern IS NULL")
    op.alter_column('dummy_epg_rules', 'pattern', existing_type=sa.Text(), nullable=False)

    op.drop_index('ix_sport_fixture_cache_kickoff', table_name='sport_fixture_cache')
    op.drop_index('ix_sport_fixture_cache_sport_type', table_name='sport_fixture_cache')
    op.drop_table('sport_fixture_cache')

    op.drop_index('ix_nfl_team_venues_team_id', table_name='nfl_team_venues')
    op.drop_table('nfl_team_venues')

    # A Live Sport category's channels only ever existed because of the now-removed refresh job -
    # delete the whole category (cascades to its playlist_channels rows) rather than just the
    # column, since there's nothing left to populate it.
    op.execute("DELETE FROM playlist_categories WHERE sport_type IS NOT NULL")
    op.drop_column('playlist_categories', 'sport_last_refresh_error')
    op.drop_column('playlist_categories', 'sport_last_refresh_status')
    op.drop_column('playlist_categories', 'sport_last_refreshed_at')
    op.drop_column('playlist_categories', 'sport_type')
    op.drop_column('playlist_categories', 'sort_mode')

    op.drop_column('playlist_channels', 'sport_event_venue_state')
    op.drop_column('playlist_channels', 'sport_event_venue_city')
    op.drop_column('playlist_channels', 'sport_event_venue_name')
    op.drop_column('playlist_channels', 'sport_event_start')
    op.drop_column('playlist_channels', 'sport_event_title')


def downgrade() -> None:
    op.add_column('playlist_channels', sa.Column('sport_event_title', sa.String(length=300), nullable=True))
    op.add_column('playlist_channels', sa.Column('sport_event_start', sa.DateTime(timezone=True), nullable=True))
    op.add_column('playlist_channels', sa.Column('sport_event_venue_name', sa.String(length=200), nullable=True))
    op.add_column('playlist_channels', sa.Column('sport_event_venue_city', sa.String(length=120), nullable=True))
    op.add_column('playlist_channels', sa.Column('sport_event_venue_state', sa.String(length=60), nullable=True))

    op.add_column(
        'playlist_categories',
        sa.Column('sort_mode', _SORT_MODE_ENUM, nullable=False, server_default='manual'),
    )
    op.add_column('playlist_categories', sa.Column('sport_type', _SPORT_TYPE_ENUM, nullable=True))
    op.add_column('playlist_categories', sa.Column('sport_last_refreshed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('playlist_categories', sa.Column('sport_last_refresh_status', sa.String(length=20), nullable=True))
    op.add_column('playlist_categories', sa.Column('sport_last_refresh_error', sa.Text(), nullable=True))

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

    op.alter_column('dummy_epg_rules', 'pattern', existing_type=sa.Text(), nullable=True)
    op.add_column('dummy_epg_rules', sa.Column('sport_type', _SPORT_TYPE_ENUM, nullable=True))
