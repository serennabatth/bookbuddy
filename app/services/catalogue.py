"""Book catalogue queries - searching, sorting, ratings and pagination."""
from dataclasses import dataclass

from sqlalchemy import func, or_

from app.extensions import db
from app.models import Book, Review
from app.services import openlibrary
from app.services.security import safe_cover_url

GENRES = [
    "Romance", "Fantasy", "Horror", "Mystery", "Non-fiction",
    "Sci-Fi", "Classics", "Dystopian", "Gothic", "Literary", "Adventure",
]

PLACEHOLDER_COVER = "https://placehold.co/400x600/EEE/AAA?text=No+Cover"

BOOKS_PER_PAGE = 24
SORT_OPTIONS = {
    "rating": "Highest rated",
    "reviews": "Most reviewed",
    "title": "Title A–Z",
    "newest": "Newest added",
}


def books_with_ratings(q: str = "", genre: str = "", sort: str = "rating", rated_only: bool = False):
    """Query for books with their average rating and review count.

    Filtering and sorting all happen in SQL, so only the current page of
    books gets loaded.
    """
    avg_rating = func.coalesce(func.avg(Review.rating), 0.0).label("avg_rating")
    review_count = func.count(Review.id).label("review_count")
    stmt = (
        db.select(Book, avg_rating, review_count)
        .outerjoin(Review, Review.book_id == Book.id)
        .group_by(Book.id)
    )
    if genre:
        stmt = stmt.where(func.lower(Book.genre) == genre.lower())
    if q:
        # autoescape so a typed % or _ is treated as a normal character
        needle = q.lower()
        stmt = stmt.where(or_(
            func.lower(Book.title).contains(needle, autoescape=True),
            func.lower(Book.author).contains(needle, autoescape=True),
        ))
    if rated_only:
        stmt = stmt.having(func.count(Review.id) > 0)
    order = {
        "rating": (avg_rating.desc(), review_count.desc(), Book.title),
        "reviews": (review_count.desc(), Book.title),
        "title": (Book.title,),
        "newest": (Book.id.desc(),),
    }.get(sort, (avg_rating.desc(), review_count.desc(), Book.title))
    return stmt.order_by(*order)


@dataclass
class BookPage:
    """One page of results plus the info needed for the page links."""
    items: list
    page: int
    total: int
    per_page: int = BOOKS_PER_PAGE

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))  # rounds up

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def paginate_books(stmt, page: int) -> BookPage:
    """Run a books_with_ratings() query for one page."""
    total = db.session.scalar(db.select(func.count()).select_from(stmt.order_by(None).subquery()))
    page = min(max(page, 1), max(1, -(-total // BOOKS_PER_PAGE)))
    rows = db.session.execute(stmt.limit(BOOKS_PER_PAGE).offset((page - 1) * BOOKS_PER_PAGE)).all()
    return BookPage(
        items=[book_dict(bk, float(avg), count) for bk, avg, count in rows],
        page=page,
        total=total,
    )


def ratings_for(book_ids) -> dict:
    """{book_id: (average rating, review count)} for several books in one query."""
    if not book_ids:
        return {}
    rows = db.session.execute(
        db.select(Review.book_id, func.avg(Review.rating), func.count(Review.id))
        .where(Review.book_id.in_(book_ids))
        .group_by(Review.book_id)
    ).all()
    return {book_id: (float(avg), count) for book_id, avg, count in rows}


def average_rating(book_id: int) -> float:
    avg = db.session.query(func.avg(Review.rating)).filter(Review.book_id == book_id).scalar()
    return float(avg or 0.0)


def book_dict(bk: Book, avg_rating: float, review_count: int = 0) -> dict:
    """Convert a Book into the dict the templates use."""
    built = openlibrary.cover_url(cover_i=str(bk.cover_i or ""), isbn=str(bk.isbn or ""), olid=str(bk.olid or ""))
    return {
        "id": bk.id,
        "title": bk.title,
        "author": bk.author,
        "genre": bk.genre or "Other",
        "year": bk.year or "",
        "cover": built or safe_cover_url(bk.cover) or PLACEHOLDER_COVER,
        "rating": avg_rating,
        "review_count": review_count,
    }
