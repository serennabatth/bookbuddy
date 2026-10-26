"""Link reviews to books by id, and move the demo books into the database

Before this, a review only stored the book's title as text, so books that
shared a title also shared reviews, and nothing stopped duplicate reviews or
ratings outside 1 to 5.

Revision ID: 62216fe78e7a
Revises: 581e5929c3cb
Create Date: 2026-10-26 15:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '62216fe78e7a'
down_revision = '581e5929c3cb'
branch_labels = None
depends_on = None

PLACEHOLDER_COVER = "https://placehold.co/400x600/EEE/AAA?text=No+Cover"

# The demo books that used to be hard-coded in main.py but were never saved
# to the database. (1984's old cover link pointed at the wrong book.)
DEMO_BOOKS = [
    ("1984", "George Orwell", "Dystopian", PLACEHOLDER_COVER),
    ("Frankenstein", "Mary Shelley", "Horror", "https://covers.openlibrary.org/b/id/8378631-L.jpg"),
    ("The Bell Jar", "Sylvia Plath", "Literary", "https://covers.openlibrary.org/b/id/8231856-L.jpg"),
    ("Moby-Dick", "Herman Melville", "Adventure", "https://covers.openlibrary.org/b/id/7222276-L.jpg"),
]


def _find_book(conn, title, author):
    """The book a review was about: same title and author, else same title."""
    book_id = conn.execute(
        sa.text("SELECT id FROM book WHERE title = :t AND author = :a ORDER BY id LIMIT 1"),
        {"t": title, "a": author or ""},
    ).scalar()
    if book_id is None:
        book_id = conn.execute(
            sa.text("SELECT id FROM book WHERE lower(title) = lower(:t) ORDER BY id LIMIT 1"),
            {"t": title},
        ).scalar()
    return book_id


def _insert_book(conn, title, author, genre, cover):
    conn.execute(
        sa.text(
            "INSERT INTO book (title, author, genre, year, cover, olid, cover_i, isbn) "
            "VALUES (:t, :a, :g, '', :c, '', '', '')"
        ),
        {"t": title, "a": author, "g": genre, "c": cover},
    )
    return _find_book(conn, title, author)


def upgrade():
    conn = op.get_bind()

    # 1. Save the demo books properly, if they aren't there already.
    for title, author, genre, cover in DEMO_BOOKS:
        if _find_book(conn, title, author) is None:
            _insert_book(conn, title, author, genre, cover)

    # 2. Add the new column, empty for now.
    with op.batch_alter_table('review', schema=None) as batch_op:
        batch_op.add_column(sa.Column('book_id', sa.Integer(), nullable=True))

    # 3. Fill it in for every existing review.
    reviews = conn.execute(
        sa.text("SELECT id, book_title, book_author, book_cover FROM review")
    ).fetchall()
    for review_id, title, author, cover in reviews:
        book_id = _find_book(conn, title, author)
        if book_id is None:
            book_id = _insert_book(conn, title, author or "Unknown", "Other", cover or PLACEHOLDER_COVER)
        conn.execute(
            sa.text("UPDATE review SET book_id = :b WHERE id = :r"),
            {"b": book_id, "r": review_id},
        )

    # 4. Make existing data obey the new rules before they are switched on:
    #    keep only each reader's newest review of a book, and keep ratings in 1-5.
    conn.execute(sa.text(
        "DELETE FROM review WHERE id NOT IN "
        "(SELECT MAX(id) FROM review GROUP BY user_id, book_id)"
    ))
    conn.execute(sa.text(
        "UPDATE review SET rating = CASE "
        "WHEN rating IS NULL OR rating < 1 THEN 1 WHEN rating > 5 THEN 5 ELSE rating END"
    ))

    # 5. Switch the rules on.
    with op.batch_alter_table('review', schema=None) as batch_op:
        batch_op.alter_column('book_id', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('rating', existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(batch_op.f('ix_review_book_id'), ['book_id'], unique=False)
        batch_op.create_foreign_key('fk_review_book_id_book', 'book', ['book_id'], ['id'])
        batch_op.create_unique_constraint('uq_review_user_book', ['user_id', 'book_id'])
        batch_op.create_check_constraint('ck_review_rating_range', 'rating >= 1 AND rating <= 5')

    # 6. An index the models already declared but the original database never had.
    op.execute("CREATE INDEX IF NOT EXISTS ix_book_olid ON book (olid)")


def downgrade():
    with op.batch_alter_table('review', schema=None) as batch_op:
        batch_op.drop_constraint('ck_review_rating_range', type_='check')
        batch_op.drop_constraint('uq_review_user_book', type_='unique')
        batch_op.drop_constraint('fk_review_book_id_book', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_review_book_id'))
        batch_op.alter_column('rating', existing_type=sa.Integer(), nullable=True)
        batch_op.drop_column('book_id')
    # The demo books and the ix_book_olid index are left in place: removing
    # them would also remove any favourites or history that point at them.
