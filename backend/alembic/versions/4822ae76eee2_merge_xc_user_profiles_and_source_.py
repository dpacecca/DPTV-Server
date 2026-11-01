"""merge xc_user profiles and source override

Revision ID: 4822ae76eee2
Revises: a9c4e2f17b30, f1a2b3c4d5e6
Create Date: 2026-11-01 05:49:18.887614

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4822ae76eee2'
down_revision: Union[str, None] = ('a9c4e2f17b30', 'f1a2b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
