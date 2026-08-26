"""add dummy epg rules

Revision ID: f3a7c2e91b6d
Revises: 8b1f2a7c9d4e
Create Date: 2026-08-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a7c2e91b6d'
down_revision: Union[str, None] = '8b1f2a7c9d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dummy_epg_rules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('playlist_id', sa.Integer(), sa.ForeignKey('playlists.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('pattern', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_dummy_epg_rules_playlist_id', 'dummy_epg_rules', ['playlist_id'])


def downgrade() -> None:
    op.drop_index('ix_dummy_epg_rules_playlist_id', table_name='dummy_epg_rules')
    op.drop_table('dummy_epg_rules')
