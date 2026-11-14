"""channel versions: backup playlists, versions, swaps (1.0.15)

Revision ID: c8d1e5f20a37
Revises: b3e7f92a4c1d
Create Date: 2026-11-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8d1e5f20a37'
down_revision: Union[str, None] = 'b3e7f92a4c1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('playlists', sa.Column('backup_for_id', sa.Integer(), nullable=True))
    op.create_index('ix_playlists_backup_for_id', 'playlists', ['backup_for_id'])
    op.create_foreign_key('fk_playlists_backup_for_id', 'playlists', 'playlists', ['backup_for_id'], ['id'], ondelete='SET NULL')

    op.add_column('playlist_channels', sa.Column('active_version_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_playlist_channels_active_version_id', 'playlist_channels', 'playlist_channels',
        ['active_version_id'], ['id'], ondelete='SET NULL',
    )
    op.add_column('playlist_channels', sa.Column('version_pinned', sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        'channel_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('main_channel_id', sa.Integer(), sa.ForeignKey('playlist_channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('candidate_channel_id', sa.Integer(), sa.ForeignKey('playlist_channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('origin', sa.String(16), nullable=False, server_default='auto'),
        sa.Column('excluded', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ok', sa.Boolean(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('fps', sa.Float(), nullable=True),
        sa.Column('bitrate_kbps', sa.Integer(), nullable=True),
        sa.Column('silent', sa.Boolean(), nullable=True),
        sa.Column('pass_streak', sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('main_channel_id', 'candidate_channel_id', name='uq_channel_version'),
    )
    op.create_index('ix_channel_versions_main_channel_id', 'channel_versions', ['main_channel_id'])
    op.create_index('ix_channel_versions_candidate_channel_id', 'channel_versions', ['candidate_channel_id'])

    op.create_table(
        'version_swaps',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('main_channel_id', sa.Integer(), sa.ForeignKey('playlist_channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('from_channel_id', sa.Integer(), nullable=True),
        sa.Column('to_channel_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_version_swaps_main_channel_id', 'version_swaps', ['main_channel_id'])


def downgrade() -> None:
    op.drop_index('ix_version_swaps_main_channel_id', table_name='version_swaps')
    op.drop_table('version_swaps')
    op.drop_index('ix_channel_versions_candidate_channel_id', table_name='channel_versions')
    op.drop_index('ix_channel_versions_main_channel_id', table_name='channel_versions')
    op.drop_table('channel_versions')
    op.drop_column('playlist_channels', 'version_pinned')
    op.drop_constraint('fk_playlist_channels_active_version_id', 'playlist_channels', type_='foreignkey')
    op.drop_column('playlist_channels', 'active_version_id')
    op.drop_constraint('fk_playlists_backup_for_id', 'playlists', type_='foreignkey')
    op.drop_index('ix_playlists_backup_for_id', table_name='playlists')
    op.drop_column('playlists', 'backup_for_id')
