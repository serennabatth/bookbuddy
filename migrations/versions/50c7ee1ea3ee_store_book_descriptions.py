"""Store each book's description instead of fetching it on every visit

Revision ID: 50c7ee1ea3ee
Revises: 847e90fc4574
Create Date: 2026-10-26 15:40:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '50c7ee1ea3ee'
down_revision = '847e90fc4574'
branch_labels = None
depends_on = None


def upgrade():
    # NULL = not fetched yet. The app fills it in the first time the book is opened.
    # A plain ADD COLUMN: SQLite can do this without rebuilding the table.
    op.add_column('book', sa.Column('description', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('book', schema=None, recreate='never') as batch_op:
        batch_op.drop_column('description')
