"""remove iptv-org scraping/catalog/mapping integration

Revision ID: 065d8af35473
Revises: d3f8b2a91c6e
Create Date: 2026-09-17 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '065d8af35473'
down_revision: Union[str, None] = 'd3f8b2a91c6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('fk_playlist_channels_iptv_org_channel_id', 'playlist_channels', type_='foreignkey')
    op.drop_index('ix_playlist_channels_iptv_org_channel_id', table_name='playlist_channels')
    op.drop_column('playlist_channels', 'iptv_org_channel_id')

    op.drop_index('ix_iptv_org_channels_channel_id', table_name='iptv_org_channels')
    op.drop_table('iptv_org_channels')

    # No code path left to serve source_kind='iptv_org' rows (url is NULL for them) - drop
    # rather than backfill, so no non-functional ghost sources are left behind.
    op.execute("DELETE FROM epg_sources WHERE source_kind = 'iptv_org'")
    op.alter_column('epg_sources', 'url', existing_type=sa.Text(), nullable=False)
    op.drop_column('epg_sources', 'iptv_org_selection')
    op.drop_column('epg_sources', 'source_kind')


def downgrade() -> None:
    op.add_column('epg_sources', sa.Column('source_kind', sa.String(length=20), nullable=False, server_default='url'))
    op.add_column('epg_sources', sa.Column('iptv_org_selection', sa.Text(), nullable=True))
    op.alter_column('epg_sources', 'url', existing_type=sa.Text(), nullable=True)
    op.alter_column('epg_sources', 'source_kind', server_default=None)

    op.create_table(
        'iptv_org_channels',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('channel_id', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('country', sa.String(length=255), nullable=True),
        sa.Column('categories', sa.String(length=255), nullable=True),
        sa.Column('site_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('logo_url', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_iptv_org_channels_channel_id', 'iptv_org_channels', ['channel_id'], unique=True)

    op.add_column('playlist_channels', sa.Column('iptv_org_channel_id', sa.Integer(), nullable=True))
    op.create_index(
        'ix_playlist_channels_iptv_org_channel_id', 'playlist_channels', ['iptv_org_channel_id']
    )
    op.create_foreign_key(
        'fk_playlist_channels_iptv_org_channel_id',
        'playlist_channels', 'iptv_org_channels',
        ['iptv_org_channel_id'], ['id'], ondelete='SET NULL',
    )
