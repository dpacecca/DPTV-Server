"""add source_channel sort_order

Revision ID: 031b56883865
Revises: e0dbd038bb1c
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '031b56883865'
down_revision: Union[str, None] = 'e0dbd038bb1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'source_channels',
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('source_channels', 'sort_order')
