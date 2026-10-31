"""add xc_user_profiles (a player profile per XC login)

Revision ID: a9c4e2f17b30
Revises: d3d6935be030
Create Date: 2026-10-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9c4e2f17b30'
down_revision: Union[str, None] = 'd3d6935be030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'xc_user_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('xc_user_id', sa.Integer(), nullable=False),
        sa.Column('data', sa.Text(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('device', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['xc_user_id'], ['xc_users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_xc_user_profiles_xc_user_id'), 'xc_user_profiles', ['xc_user_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_xc_user_profiles_xc_user_id'), table_name='xc_user_profiles')
    op.drop_table('xc_user_profiles')
