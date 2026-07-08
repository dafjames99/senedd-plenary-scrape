"""polymorphic embeddings: add source_type and source_id

Revision ID: 7a0d6f8bf14c
Revises: a4f19a6ba949
Create Date: 2026-06-19 00:09:32.784567

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a0d6f8bf14c'
down_revision: Union[str, Sequence[str], None] = 'a4f19a6ba949'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Generalise speech_embeddings to a polymorphic (source_type, source_id) key.

    Existing rows are all speeches, so they backfill to source_type='speech',
    source_id=speech_id. ``source_id`` is added nullable, backfilled, then set
    NOT NULL so the migration is safe against the populated corpus. ``speech_id``
    is relaxed to nullable (kept this release as a rollback safety net); its
    cascade FK is retained for now and dropped in a later migration.
    """
    op.add_column(
        'speech_embeddings',
        sa.Column('source_type', sa.String(length=20), server_default='speech', nullable=False),
    )
    # Add nullable, backfill from the legacy speech_id, then enforce NOT NULL.
    op.add_column('speech_embeddings', sa.Column('source_id', sa.Integer(), nullable=True))
    op.execute("UPDATE speech_embeddings SET source_id = speech_id WHERE source_id IS NULL")
    op.alter_column('speech_embeddings', 'source_id', existing_type=sa.INTEGER(), nullable=False)

    op.alter_column(
        'speech_embeddings', 'speech_id',
        existing_type=sa.INTEGER(),
        nullable=True,
    )
    op.create_index(
        'ix_speech_embeddings_source',
        'speech_embeddings',
        ['source_type', 'source_id', 'model_name'],
        unique=False,
    )


def downgrade() -> None:
    """Revert to the speech-only schema.

    Only valid while every embedding is a speech (speech_id populated); any
    non-speech row (speech_id NULL) would violate the restored NOT NULL and the
    downgrade will fail by design rather than silently drop data.
    """
    op.drop_index('ix_speech_embeddings_source', table_name='speech_embeddings')
    op.alter_column(
        'speech_embeddings', 'speech_id',
        existing_type=sa.INTEGER(),
        nullable=False,
    )
    op.drop_column('speech_embeddings', 'source_id')
    op.drop_column('speech_embeddings', 'source_type')
