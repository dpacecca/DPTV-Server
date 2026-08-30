"""add iptv-org epg source kind

Revision ID: a1c9e6f24d8b
Revises: 7d4e1f8a3c2b
Create Date: 2026-08-30 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c9e6f24d8b'
down_revision: Union[str, None] = '7d4e1f8a3c2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('epg_sources', sa.Column('source_kind', sa.String(length=20), nullable=False, server_default='url'))
    op.add_column('epg_sources', sa.Column('iptv_org_selection', sa.Text(), nullable=True))
    op.alter_column('epg_sources', 'url', existing_type=sa.Text(), nullable=True)
    op.alter_column('epg_sources', 'source_kind', server_default=None)


def downgrade() -> None:
    op.execute("UPDATE epg_sources SET url = '' WHERE url IS NULL")
    op.alter_column('epg_sources', 'url', existing_type=sa.Text(), nullable=False)
    op.drop_column('epg_sources', 'iptv_org_selection')
    op.drop_column('epg_sources', 'source_kind')
