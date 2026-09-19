"""add playlist_category dummy_epg_mode + rule pin

Revision ID: c4f8b2e71a9d
Revises: b7f4a1c93d2e
Create Date: 2026-09-19 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4f8b2e71a9d'
down_revision: Union[str, None] = 'b7f4a1c93d2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'playlist_categories',
        sa.Column(
            'dummy_epg_mode',
            sa.Enum('inherit', 'off', 'name', 'event', native_enum=False, length=20, name='dummyepgmode'),
            nullable=False,
            server_default='off',
        ),
    )
    op.add_column('playlist_categories', sa.Column('dummy_epg_rule_id', sa.Integer(), nullable=True))
    op.create_index('ix_playlist_categories_dummy_epg_rule_id', 'playlist_categories', ['dummy_epg_rule_id'])
    op.create_foreign_key(
        'fk_playlist_categories_dummy_epg_rule_id',
        'playlist_categories', 'dummy_epg_rules',
        ['dummy_epg_rule_id'], ['id'], ondelete='SET NULL',
    )

    # Replaces dummy_epg_for_unassigned (a bool that was never actually exposed in the UI) with
    # the same 3-value mode the channel level already uses - preserve exactly what it used to
    # mean (True -> "name" mode) before dropping it.
    op.execute("UPDATE playlist_categories SET dummy_epg_mode = 'name' WHERE dummy_epg_for_unassigned IS TRUE")
    op.alter_column('playlist_categories', 'dummy_epg_mode', server_default=None)
    op.drop_column('playlist_categories', 'dummy_epg_for_unassigned')


def downgrade() -> None:
    op.add_column(
        'playlist_categories', sa.Column('dummy_epg_for_unassigned', sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.execute("UPDATE playlist_categories SET dummy_epg_for_unassigned = TRUE WHERE dummy_epg_mode = 'name'")
    op.alter_column('playlist_categories', 'dummy_epg_for_unassigned', server_default=None)

    op.drop_constraint('fk_playlist_categories_dummy_epg_rule_id', 'playlist_categories', type_='foreignkey')
    op.drop_index('ix_playlist_categories_dummy_epg_rule_id', table_name='playlist_categories')
    op.drop_column('playlist_categories', 'dummy_epg_rule_id')
    op.drop_column('playlist_categories', 'dummy_epg_mode')
