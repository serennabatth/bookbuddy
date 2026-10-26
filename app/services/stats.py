"""Reading stats for the Your Library page."""
from datetime import datetime

from sqlalchemy import func

from app.extensions import db
from app.models import Book, Review, ShelfEntry


def reading_stats(user_id: int, year: int) -> dict:
    """Stats for one calendar year: books finished, reviews, average rating and top genre."""
    start, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    finished_this_year = (
        ShelfEntry.user_id == user_id,
        ShelfEntry.status == "finished",
        ShelfEntry.finished_at >= start,
        ShelfEntry.finished_at < end,
    )

    finished = db.session.scalar(db.select(func.count(ShelfEntry.id)).where(*finished_this_year))

    review_count, avg_rating = db.session.execute(
        db.select(func.count(Review.id), func.avg(Review.rating))
        .where(Review.user_id == user_id, Review.created >= start, Review.created < end)
    ).one()

    # most common genre among the books finished this year
    top_genre = db.session.scalar(
        db.select(Book.genre)
        .join(ShelfEntry, ShelfEntry.book_id == Book.id)
        .where(*finished_this_year)
        .group_by(Book.genre)
        .order_by(func.count(ShelfEntry.id).desc(), Book.genre)
        .limit(1)
    )

    return {
        "year": year,
        "finished": finished or 0,
        "reviews": review_count or 0,
        "avg_rating": round(float(avg_rating), 1) if avg_rating is not None else None,
        "top_genre": top_genre,
    }
