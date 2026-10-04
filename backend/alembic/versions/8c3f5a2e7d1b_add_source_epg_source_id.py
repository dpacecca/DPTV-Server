"""add source epg_source_id

Revision ID: 8c3f5a2e7d1b
Revises: 4e1b8d6c2a9f
Create Date: 2026-10-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8c3f5a2e7d1b'
down_revision: Union[str, None] = '4e1b8d6c2a9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sources', sa.Column('epg_source_id', sa.Integer(), nullable=True))
    op.create_index('ix_sources_epg_source_id', 'sources', ['epg_source_id'])
    op.create_foreign_key(
        'fk_sources_epg_source_id', 'sources', 'epg_sources', ['epg_source_id'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('fk_sources_epg_source_id', 'sources', type_='foreignkey')
    op.drop_index('ix_sources_epg_source_id', table_name='sources')
    op.drop_column('sources', 'epg_source_id')
