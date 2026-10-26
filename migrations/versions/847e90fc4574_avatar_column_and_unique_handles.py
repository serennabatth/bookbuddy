"""Add the user avatar column and make handles unique

Profile pictures were saved to disk but the path was thrown away, because
the user table had no column for it. Handles could also be shared by two
accounts, so a public profile link could point at the wrong person.

Revision ID: 847e90fc4574
Revises: 62216fe78e7a
Create Date: 2026-10-26 15:20:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '847e90fc4574'
down_revision = '62216fe78e7a'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. A place to store each user's profile picture path. (Skipped if an
    #    earlier, interrupted run of this migration already added it.)
    existing = {col["name"] for col in sa.inspect(conn).get_columns("user")}
    if "avatar" not in existing:
        op.add_column('user', sa.Column('avatar', sa.String(length=255), nullable=True))

    # "user" is quoted because it is a reserved word in PostgreSQL.
    # 2. An empty handle means "not chosen yet": store it as NULL, which the
    #    unique rule allows on any number of accounts.
    conn.execute(sa.text(
        "UPDATE \"user\" SET handle = NULL WHERE handle IS NULL OR trim(handle) = '' OR trim(handle) = '@'"
    ))
    conn.execute(sa.text("UPDATE \"user\" SET handle = lower(trim(handle)) WHERE handle IS NOT NULL"))

    # 3. Where two accounts share a handle, the older account keeps it and
    #    the newer one gets a number added: @name -> @name2 (or @name3, ...).
    rows = conn.execute(sa.text(
        "SELECT id, handle FROM \"user\" WHERE handle IS NOT NULL ORDER BY id"
    )).fetchall()
    used = set()
    for user_id, handle in rows:
        new_handle = handle
        number = 2
        while new_handle in used:
            new_handle = f"{handle}{number}"
            number += 1
        used.add(new_handle)
        if new_handle != handle:
            conn.execute(
                sa.text("UPDATE \"user\" SET handle = :h WHERE id = :id"),
                {"h": new_handle, "id": user_id},
            )

    # 4. Switch the rule on with a unique index. (A unique *constraint* would
    #    make SQLite rebuild the whole user table, which it refuses to do while
    #    reviews, favourites and history point at it. An index needs no rebuild
    #    and enforces exactly the same rule, in SQLite and PostgreSQL.)
    op.create_index('uq_user_handle', 'user', ['handle'], unique=True)


def downgrade():
    op.drop_index('uq_user_handle', table_name='user')
    # recreate="never": drop the column in place instead of rebuilding the table.
    with op.batch_alter_table('user', schema=None, recreate='never') as batch_op:
        batch_op.drop_column('avatar')
    # Renamed handles are not changed back: the old duplicate can't be restored safely.
