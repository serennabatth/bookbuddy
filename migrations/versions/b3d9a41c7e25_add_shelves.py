"""Add shelves (want to read, currently reading, finished)

Revision ID: b3d9a41c7e25
Revises: f582c23f6824
Create Date: 2026-10-26 23:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d9a41c7e25'
down_revision = 'f582c23f6824'
branch_labels = None
depends_on = None


def upgrade():
    # new table, so nothing existing needs rebuilding
    op.create_table(
        'shelf_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('added_at', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['book_id'], ['book.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'book_id', name='uq_shelf_user_book'),
        sa.CheckConstraint("status IN ('want', 'reading', 'finished')", name='ck_shelf_status'),
    )
    op.create_index('ix_shelf_entry_book_id', 'shelf_entry', ['book_id'])
    op.create_index('ix_shelf_entry_user_status', 'shelf_entry', ['user_id', 'status'])


def downgrade():
    op.drop_index('ix_shelf_entry_user_status', table_name='shelf_entry')
    op.drop_index('ix_shelf_entry_book_id', table_name='shelf_entry')
    op.drop_table('shelf_entry')
