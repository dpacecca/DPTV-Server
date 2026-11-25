"""movie categories: provider film fields, TMDB film info and trending (1.0.24)

Revision ID: e5b2c8d14a90
Revises: d4a9e1c7b2f8
Create Date: 2026-11-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5b2c8d14a90'
down_revision: Union[str, None] = 'd4a9e1c7b2f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('source_channels', sa.Column('provider_release', sa.String(10), nullable=True))
    op.add_column('source_channels', sa.Column('provider_tmdb_id', sa.String(32), nullable=True))
    op.add_column('source_channels', sa.Column('provider_rating', sa.Float(), nullable=True))
    op.create_table(
        'movie_info',
        sa.Column('source_channel_id', sa.Integer(), sa.ForeignKey('source_channels.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tmdb_id', sa.Integer(), nullable=True),
        sa.Column('release_date', sa.String(10), nullable=True),
        sa.Column('rating', sa.Float(), nullable=True),
        sa.Column('popularity', sa.Float(), nullable=True),
        sa.Column('genres', sa.Text(), nullable=True),
        sa.Column('tmdb_checked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('looked_up_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_movie_info_tmdb_id', 'movie_info', ['tmdb_id'])
    op.create_table(
        'tmdb_trending',
        sa.Column('tmdb_id', sa.Integer(), primary_key=True),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('tmdb_trending')
    op.drop_index('ix_movie_info_tmdb_id', table_name='movie_info')
    op.drop_table('movie_info')
    op.drop_column('source_channels', 'provider_rating')
    op.drop_column('source_channels', 'provider_tmdb_id')
    op.drop_column('source_channels', 'provider_release')
