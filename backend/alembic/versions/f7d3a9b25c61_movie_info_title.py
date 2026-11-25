"""movie info keeps TMDB's title (1.0.26)

Revision ID: f7d3a9b25c61
Revises: e5b2c8d14a90
Create Date: 2026-11-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7d3a9b25c61'
down_revision: Union[str, None] = 'e5b2c8d14a90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('movie_info', sa.Column('title', sa.String(300), nullable=True))
    # Films already matched are asked for again, by their TMDB id, to fill in the title.
    op.execute("UPDATE movie_info SET tmdb_checked = false WHERE tmdb_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column('movie_info', 'title')
