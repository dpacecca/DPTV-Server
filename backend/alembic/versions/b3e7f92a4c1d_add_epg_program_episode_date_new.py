"""add epg_program episode_num, program_date, is_new

Revision ID: b3e7f92a4c1d
Revises: 4822ae76eee2
Create Date: 2026-11-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3e7f92a4c1d'
down_revision: Union[str, None] = '4822ae76eee2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('epg_programs', sa.Column('episode_num', sa.String(length=100), nullable=True))
    op.add_column('epg_programs', sa.Column('program_date', sa.String(length=20), nullable=True))
    op.add_column('epg_programs', sa.Column('is_new', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('epg_programs', 'is_new', server_default=None)


def downgrade() -> None:
    op.drop_column('epg_programs', 'is_new')
    op.drop_column('epg_programs', 'program_date')
    op.drop_column('epg_programs', 'episode_num')
