"""add embedding_cache table

Revision ID: ad71d7f96137
Revises: fccbf37e6bae
Create Date: 2026-06-25 18:33:22.652897

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'ad71d7f96137'
down_revision: Union[str, Sequence[str], None] = 'fccbf37e6bae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'embedding_cache',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('text_hash', sa.String(length=64), nullable=False),
        sa.Column('model_name', sa.String(length=100), nullable=False),
        sa.Column('embedding_vector', Vector(), nullable=False),
        sa.Column('embed_config_version', sa.String(length=50), nullable=True),
        sa.Column('char_len', sa.Integer(), nullable=True),
        sa.Column('hit_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('text_hash', 'model_name', name='uq_embedding_cache_hash_model'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('embedding_cache')
