"""add playlist_channel dummy_epg_rule_id

Revision ID: e91dc4a711a4
Revises: 065d8af35473
Create Date: 2026-09-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e91dc4a711a4'
down_revision: Union[str, None] = '065d8af35473'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('playlist_channels', sa.Column('dummy_epg_rule_id', sa.Integer(), nullable=True))
    op.create_index('ix_playlist_channels_dummy_epg_rule_id', 'playlist_channels', ['dummy_epg_rule_id'])
    op.create_foreign_key(
        'fk_playlist_channels_dummy_epg_rule_id',
        'playlist_channels', 'dummy_epg_rules',
        ['dummy_epg_rule_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_playlist_channels_dummy_epg_rule_id', 'playlist_channels', type_='foreignkey')
    op.drop_index('ix_playlist_channels_dummy_epg_rule_id', table_name='playlist_channels')
    op.drop_column('playlist_channels', 'dummy_epg_rule_id')
