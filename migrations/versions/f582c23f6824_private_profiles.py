"""Store each user's private-profile setting

Before this, the privacy toggle was saved in the visitor's browser cookie
and did nothing.

Revision ID: f582c23f6824
Revises: 6a7902d6b6c2
Create Date: 2026-10-26 17:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f582c23f6824'
down_revision = '6a7902d6b6c2'
branch_labels = None
depends_on = None


def upgrade():
    # A plain ADD COLUMN (no table rebuild). server_default gives every
    # existing account the value "not private".
    op.add_column('user', sa.Column('is_private', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('user', schema=None, recreate='never') as batch_op:
        batch_op.drop_column('is_private')
