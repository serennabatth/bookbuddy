"""Add the follow table, replacing the hard-coded follower lists

Revision ID: 6a7902d6b6c2
Revises: 50c7ee1ea3ee
Create Date: 2026-10-26 16:45:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a7902d6b6c2'
down_revision = '50c7ee1ea3ee'
branch_labels = None
depends_on = None


def upgrade():
    # A brand-new table, so no existing table has to be rebuilt.
    op.create_table(
        'follow',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('follower_id', sa.Integer(), nullable=False),
        sa.Column('followed_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['follower_id'], ['user.id']),
        sa.ForeignKeyConstraint(['followed_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('follower_id', 'followed_id', name='uq_follow_pair'),
        sa.CheckConstraint('follower_id != followed_id', name='ck_follow_not_self'),
    )
    op.create_index('ix_follow_follower_id', 'follow', ['follower_id'])
    op.create_index('ix_follow_followed_id', 'follow', ['followed_id'])


def downgrade():
    op.drop_index('ix_follow_followed_id', table_name='follow')
    op.drop_index('ix_follow_follower_id', table_name='follow')
    op.drop_table('follow')
