"""add speech_fidelity table

Revision ID: 2d7b930b3b8a
Revises: ad71d7f96137
Create Date: 2026-06-26 01:27:17.222928

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d7b930b3b8a'
down_revision: Union[str, Sequence[str], None] = 'ad71d7f96137'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'speech_fidelity',
        sa.Column('speech_id', sa.Integer(), nullable=False),
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('wpm', sa.Float(), nullable=True),
        sa.Column('ends_midsentence', sa.Boolean(), nullable=True),
        sa.Column('flag', sa.String(length=20), nullable=False, server_default='ok'),
        sa.Column('is_suspect', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('computed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['speech_id'], ['speeches.speech_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('speech_id'),
    )
    op.create_index(
        op.f('ix_speech_fidelity_is_suspect'), 'speech_fidelity', ['is_suspect'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_speech_fidelity_is_suspect'), table_name='speech_fidelity')
    op.drop_table('speech_fidelity')
