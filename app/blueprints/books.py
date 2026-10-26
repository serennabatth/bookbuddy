"""Home, browsing, book pages and book search."""
from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.models import Book, Review, utc_now
from app.services import openlibrary
from app.services.catalogue import (
    GENRES, PLACEHOLDER_COVER, SORT_OPTIONS,
    average_rating, book_dict, books_with_ratings, paginate_books,
)
from app.services.security import safe_cover_url
from app.services.shelves import PICKER_OPTIONS, shelf_book_dicts, shelf_for
from app.services.users import display_name

bp = Blueprint("books", __name__)


def _page_number() -> int:
    try:
        return int(request.args.get("page", 1))
    except ValueError:
        return 1


def _time_ago(dt: datetime | None) -> str:
    """e.g. "3h ago", "2d ago"."""
    if not dt:
        return ""
    now = utc_now()
    diff = now - dt
    seconds = int(diff.total_seconds())

    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks}w ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


REVIEW_SORTS = {"newest": "Newest", "highest": "Highest rated", "lowest": "Lowest rated"}


def _review_sort() -> str:
    sort = request.args.get("sort")
    return sort if sort in REVIEW_SORTS else "newest"


def _reviews_for(book_id: int, sort: str, limit: int | None = None) -> list[dict]:
    """A book's reviews in the chosen order, ready for the templates."""
    order = {
        "newest": (Review.created.desc(), Review.id.desc()),
        "highest": (Review.rating.desc(), Review.created.desc()),
        "lowest": (Review.rating.asc(), Review.created.desc()),
    }[sort]
    query = Review.query.filter_by(book_id=book_id).order_by(*order)
    if limit:
        query = query.limit(limit)
    viewer_id = current_user.id if current_user.is_authenticated else None
    return [
        {
            "id": r.id,
            "user": display_name(r.user),
            "rating": r.rating,
            "text": (r.text or "").strip(),
            "age": _time_ago(r.created),
            "is_mine": viewer_id is not None and r.user_id == viewer_id,
        }
        for r in query.all()
    ]


@bp.route("/")
def home():
    # Top Rated only shows books that have reviews
    rows = db.session.execute(books_with_ratings(rated_only=True).limit(6)).all()
    top_rated = [book_dict(bk, float(avg), count) for bk, avg, count in rows]
    first_book = db.session.scalar(db.select(Book).order_by(Book.title).limit(1))
    featured = top_rated[0] if top_rated else (book_dict(first_book, 0.0) if first_book else {})
    reading = shelf_book_dicts(current_user.id, "reading") if current_user.is_authenticated else []
    return render_template(
        "index.html",
        featured=featured,
        reading=reading,
        genres=GENRES,
        top_rated=top_rated,
    )


@bp.route("/browse")
def browse():
    genre = (request.args.get("genre") or "").strip()
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort") if request.args.get("sort") in SORT_OPTIONS else "rating"

    results = paginate_books(books_with_ratings(q=q, genre=genre, sort=sort), _page_number())
    return render_template(
        "browse.html",
        books=results.items,
        results=results,
        genres=GENRES,
        selected_genre=genre,
        sort=sort,
        sort_options=SORT_OPTIONS,
    )


@bp.route("/book-details/<path:title>")
def book_details_by_title(title):
    """Redirect old /book-details/<title> links to the new id-based URL."""
    bk = (
        Book.query.filter_by(title=title).order_by(Book.id).first()
        or Book.query.filter(func.lower(Book.title) == title.lower()).order_by(Book.id).first()
    )
    if not bk:
        abort(404)
    return redirect(url_for("books.book_details", book_id=bk.id), code=301)


@bp.route("/books/<int:book_id>")
def book_details(book_id):
    bk = db.get_or_404(Book, book_id)

    sort = _review_sort()
    viewer_id = current_user.id if current_user.is_authenticated else None
    my_review = (
        Review.query.filter_by(user_id=viewer_id, book_id=bk.id).first() if viewer_id else None
    )
    my_shelf = shelf_for(viewer_id, bk.id) if viewer_id else None

    # get the description from Open Library the first time, then save it
    if bk.description is None:
        fetched = openlibrary.description_for(bk.title, bk.author)
        if fetched is not None:          # None = couldn't reach Open Library, try again next time
            bk.description = fetched     # "" if there isn't one
            db.session.commit()
    description = bk.description or ""

    return render_template(
        "book_details.html",
        book=book_dict(bk, average_rating(bk.id)),
        book_id=bk.id,
        description=description,
        reviews=_reviews_for(bk.id, sort, limit=25),
        review_count=Review.query.filter_by(book_id=bk.id).count(),
        sort=sort,
        sort_options=REVIEW_SORTS,
        my_review_id=my_review.id if my_review else None,
        shelf=my_shelf.status if my_shelf else None,
        shelf_options=PICKER_OPTIONS,
    )


@bp.route("/books/<int:book_id>/reviews")
def book_reviews(book_id):
    bk = db.get_or_404(Book, book_id)
    sort = _review_sort()
    reviews = _reviews_for(bk.id, sort)
    return render_template(
        "book_reviews.html",
        book=book_dict(bk, average_rating(bk.id)),
        reviews=reviews,
        avg_rating=average_rating(bk.id),
        review_count=len(reviews),
        sort=sort,
        sort_options=REVIEW_SORTS,
    )


@bp.route("/add-book", methods=["GET", "POST"])
@login_required
def add_book():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        author = (request.form.get("author") or "").strip()
        genre = (request.form.get("genre") or "").strip() or "Other"
        year = (request.form.get("year") or "").strip()
        cover = safe_cover_url(request.form.get("cover"))

        if not title or not author:
            flash("Title and author are required.", "error")
            return redirect(url_for("books.add_book"))

        existing = Book.query.filter_by(title=title, author=author).first()
        if existing:
            flash("That book already exists.", "error")
            return redirect(url_for("reviews.add_review"))

        meta = openlibrary.best_match(title, author) or {}
        best_cover = safe_cover_url(meta.get("cover")) or cover or PLACEHOLDER_COVER

        b = Book(
            title=title,
            author=author,
            genre=genre,
            year=year or meta.get("year", "") or "",
            cover=best_cover,
            olid=meta.get("olid", "") or "",
            cover_i=meta.get("cover_i", "") or "",
            isbn=meta.get("isbn", "") or "",
        )
        db.session.add(b)
        db.session.commit()

        flash("Book added!", "ok")
        return redirect(url_for("reviews.add_review"))

    return render_template("add_book.html", genres=GENRES)


@bp.route("/top-rated")
def top_rated():
    q = (request.args.get("q") or "").strip()
    results = paginate_books(books_with_ratings(q=q, rated_only=True), _page_number())
    return render_template("top_rated.html", books=results.items, results=results)


@bp.route("/genre/<name>")
def genre_page(name):
    return redirect(url_for("books.browse", genre=name), code=302)


@bp.get("/api/search")
def api_search_books():
    """Search-as-you-type on the Add Review page (max 8 results)."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    rows = db.session.execute(books_with_ratings(q=q, sort="title").limit(8)).all()
    return jsonify([book_dict(bk, float(avg), count) for bk, avg, count in rows])


@bp.get("/api/openlibrary")
def api_openlibrary():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    return jsonify(openlibrary.search(q))
