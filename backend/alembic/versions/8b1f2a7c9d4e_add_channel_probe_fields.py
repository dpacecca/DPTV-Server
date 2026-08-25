"""add channel probe fields

Revision ID: 8b1f2a7c9d4e
Revises: c644b625347b
Create Date: 2026-08-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8b1f2a7c9d4e'
down_revision: Union[str, None] = 'c644b625347b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All nullable, no backfill needed - a channel simply hasn't been scanned yet until an
    # admin runs "scan for duplicates" on its category.
    op.add_column('playlist_channels', sa.Column('detected_width', sa.Integer(), nullable=True))
    op.add_column('playlist_channels', sa.Column('detected_height', sa.Integer(), nullable=True))
    op.add_column('playlist_channels', sa.Column('detected_fps', sa.Float(), nullable=True))
    op.add_column('playlist_channels', sa.Column('detected_bitrate_kbps', sa.Integer(), nullable=True))
    op.add_column(
        'playlist_channels',
        sa.Column(
            'probe_status',
            sa.Enum('ok', 'timeout', 'error', 'unreachable', 'no_video_stream', 'no_url', native_enum=False, length=20, name='probestatus'),
            nullable=True,
        ),
    )
    op.add_column('playlist_channels', sa.Column('last_probed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('playlist_channels', 'last_probed_at')
    op.drop_column('playlist_channels', 'probe_status')
    op.drop_column('playlist_channels', 'detected_bitrate_kbps')
    op.drop_column('playlist_channels', 'detected_fps')
    op.drop_column('playlist_channels', 'detected_height')
    op.drop_column('playlist_channels', 'detected_width')
