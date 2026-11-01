"""add xc_user source_override_id

Revision ID: f1a2b3c4d5e6
Revises: d3d6935be030
Create Date: 2026-11-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'd3d6935be030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('xc_users', sa.Column('source_override_id', sa.Integer(), nullable=True))
    op.create_index('ix_xc_users_source_override_id', 'xc_users', ['source_override_id'])
    op.create_foreign_key(
        'fk_xc_users_source_override_id', 'xc_users', 'sources', ['source_override_id'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('fk_xc_users_source_override_id', 'xc_users', type_='foreignkey')
    op.drop_index('ix_xc_users_source_override_id', table_name='xc_users')
    op.drop_column('xc_users', 'source_override_id')
