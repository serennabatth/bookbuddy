"""Shelves - want to read, currently reading and finished."""
from sqlalchemy import func

from app.extensions import db
from app.models import Book, ShelfEntry, utc_now
from app.services.catalogue import book_dict, ratings_for

SHELVES = {
    "want": "Want to read",
    "reading": "Currently reading",
    "finished": "Finished",
}

# shorter labels for the buttons on the book page
PICKER_OPTIONS = [("want", "Want to read"), ("reading", "Reading"), ("finished", "Finished")]


def shelf_for(user_id: int, book_id: int) -> ShelfEntry | None:
    return ShelfEntry.query.filter_by(user_id=user_id, book_id=book_id).first()


def set_shelf(user_id: int, book_id: int, status: str | None) -> ShelfEntry | None:
    """Put a book on a shelf, move it to another one, or take it off (status None).

    Also keeps track of when the book was started and finished.
    """
    if status is not None and status not in SHELVES:
        raise ValueError(f"Unknown shelf: {status!r}")

    entry = shelf_for(user_id, book_id)
    if status is None:
        if entry:
            db.session.delete(entry)
            db.session.commit()
        return None

    if entry is None:
        entry = ShelfEntry(user_id=user_id, book_id=book_id, status=status)
        db.session.add(entry)
    elif entry.status == status:
        return entry   # already on this shelf, nothing to change

    now = utc_now()
    entry.status = status
    if status == "want":
        entry.started_at = entry.finished_at = None
    elif status == "reading":
        entry.started_at = entry.started_at or now
        entry.finished_at = None
    else:  # finished
        entry.finished_at = now

    db.session.commit()
    return entry


def shelf_counts(user_id: int) -> dict[str, int]:
    """How many books are on each shelf, e.g. {"want": 3, "reading": 1, "finished": 0}."""
    rows = db.session.execute(
        db.select(ShelfEntry.status, func.count(ShelfEntry.id))
        .where(ShelfEntry.user_id == user_id)
        .group_by(ShelfEntry.status)
    ).all()
    counts = {status: 0 for status in SHELVES}
    counts.update(dict(rows))
    return counts


def books_on_shelf(user_id: int, status: str, limit: int | None = None) -> list[tuple[Book, ShelfEntry]]:
    """(book, entry) pairs on one shelf, most recently moved there first."""
    latest = func.coalesce(ShelfEntry.finished_at, ShelfEntry.started_at, ShelfEntry.added_at)
    stmt = (
        db.select(Book, ShelfEntry)
        .join(ShelfEntry, ShelfEntry.book_id == Book.id)
        .where(ShelfEntry.user_id == user_id, ShelfEntry.status == status)
        .order_by(latest.desc(), ShelfEntry.id.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return [(book, entry) for book, entry in db.session.execute(stmt).all()]


def shelf_book_dicts(user_id: int, status: str, limit: int | None = None) -> list[dict]:
    """Books on a shelf in the format the templates use, plus started/finished dates."""
    rows = books_on_shelf(user_id, status, limit)
    ratings = ratings_for([b.id for b, _ in rows])
    return [
        {**book_dict(b, *ratings.get(b.id, (0.0, 0))), "started_at": entry.started_at, "finished_at": entry.finished_at}
        for b, entry in rows
    ]
