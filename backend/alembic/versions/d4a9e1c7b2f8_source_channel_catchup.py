"""source channels keep the provider's catch-up (1.0.23)

Revision ID: d4a9e1c7b2f8
Revises: c8d1e5f20a37
Create Date: 2026-11-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4a9e1c7b2f8'
down_revision: Union[str, None] = 'c8d1e5f20a37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('source_channels', sa.Column('tv_archive', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('source_channels', sa.Column('tv_archive_duration', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('source_channels', 'tv_archive_duration')
    op.drop_column('source_channels', 'tv_archive')
