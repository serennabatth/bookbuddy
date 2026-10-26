"""The reader's own lists - favourites and recently viewed."""
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Book, Favourite, History, utc_now
from app.services.catalogue import book_dict, ratings_for
from app.services.security import safe_next_url
from app.services.shelves import SHELVES, set_shelf, shelf_book_dicts, shelf_counts
from app.services.stats import reading_stats

bp = Blueprint("library", __name__)


def _matches(book: Book, q: str) -> bool:
    """True if the search text is in the title or author (or there's no search)."""
    return not q or q in (book.title or "").lower() or q in (book.author or "").lower()


def _book_from_json() -> Book | None:
    """Get the book from {"book_id": ...} in the request body, or None."""
    data = request.get_json(silent=True) or {}
    try:
        return db.session.get(Book, int(data.get("book_id") or 0))
    except (TypeError, ValueError):
        return None


ROW_LIMIT = 12   # books shown in each row on the Your Library page


@bp.route("/library")
@login_required
def your_library():
    counts = shelf_counts(current_user.id)

    fav_books = (
        db.session.query(Book)
        .join(Favourite, Favourite.book_id == Book.id)
        .filter(Favourite.user_id == current_user.id)
        .order_by(Favourite.created_at.desc())
        .all()
    )
    recent_books = (
        db.session.query(Book)
        .join(History, History.book_id == Book.id)
        .filter(History.user_id == current_user.id)
        .order_by(History.viewed_at.desc())
        .limit(10)
        .all()
    )
    ratings = ratings_for([b.id for b in fav_books + recent_books])

    def as_dicts(books):
        return [book_dict(b, *ratings.get(b.id, (0.0, 0))) for b in books]

    is_empty = not any(counts.values()) and not fav_books
    return render_template(
        "library.html",
        counts=counts,
        total=sum(counts.values()),
        stats=reading_stats(current_user.id, utc_now().year),
        reading=shelf_book_dicts(current_user.id, "reading"),
        want=shelf_book_dicts(current_user.id, "want", ROW_LIMIT),
        finished=shelf_book_dicts(current_user.id, "finished", ROW_LIMIT),
        favourites=as_dicts(fav_books[:ROW_LIMIT]),
        favourite_count=len(fav_books),
        favourite_ids={b.id for b in fav_books},
        recent=as_dicts(recent_books),
        is_empty=is_empty,
    )


@bp.route("/library/<status>")
@login_required
def shelf_page(status):
    """Every book on one shelf (the "See all" link)."""
    if status not in SHELVES:
        abort(404)
    fav_ids = {f.book_id for f in Favourite.query.filter_by(user_id=current_user.id).all()}
    return render_template(
        "library_shelf.html",
        status=status,
        shelf_name=SHELVES[status],
        books=shelf_book_dicts(current_user.id, status),
        favourite_ids=fav_ids,
    )


@bp.route("/favourites")
@login_required
def favourites():
    q = (request.args.get("q") or "").strip().lower()
    rows = (
        db.session.query(Book)
        .join(Favourite, Favourite.book_id == Book.id)
        .filter(Favourite.user_id == current_user.id)
        .order_by(Favourite.created_at.desc())
        .all()
    )
    ratings = ratings_for([b.id for b in rows])
    books = [book_dict(b, *ratings.get(b.id, (0.0, 0))) for b in rows if _matches(b, q)]
    return render_template("favourites.html", books=books, q=q)


@bp.post("/api/favourites/toggle")
@login_required
def api_favourites_toggle():
    book = _book_from_json()
    if book is None:
        return jsonify({"ok": False, "error": "That book doesn't exist."}), 404

    fav = Favourite.query.filter_by(user_id=current_user.id, book_id=book.id).first()
    if fav:
        db.session.delete(fav)
        db.session.commit()
        return jsonify({"ok": True, "favourited": False})

    db.session.add(Favourite(user_id=current_user.id, book_id=book.id))
    db.session.commit()
    return jsonify({"ok": True, "favourited": True})


@bp.get("/api/favourites")
@login_required
def api_favourites_list():
    ids = [f.book_id for f in Favourite.query.filter_by(user_id=current_user.id).all()]
    return jsonify({"ok": True, "book_ids": ids})


@bp.route("/history")
@login_required
def history():
    q = (request.args.get("q") or "").strip().lower()
    rows = (
        db.session.query(Book, History.viewed_at)
        .join(History, History.book_id == Book.id)
        .filter(History.user_id == current_user.id)
        .order_by(History.viewed_at.desc())
        .limit(100)
        .all()
    )
    ratings = ratings_for([b.id for b, _ in rows])
    books = [
        {**book_dict(b, *ratings.get(b.id, (0.0, 0))), "viewed_at": viewed_at}
        for b, viewed_at in rows
        if _matches(b, q)
    ]
    return render_template("history.html", books=books, q=q)


@bp.post("/api/history/add")
@login_required
def api_history_add():
    book = _book_from_json()
    if book is None:
        return jsonify({"ok": False, "error": "That book doesn't exist."}), 404

    row = History.query.filter_by(user_id=current_user.id, book_id=book.id).first()
    if row:
        row.viewed_at = utc_now()
    else:
        db.session.add(History(user_id=current_user.id, book_id=book.id))
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/books/<int:book_id>/shelf")
@login_required
def set_book_shelf(book_id):
    """Put a book on a shelf, move it, or take it off (empty status).

    A plain form post, so it works without JavaScript. "next" says where to
    go afterwards (e.g. back to Home after "Finished it").
    """
    book = db.get_or_404(Book, book_id)
    status = (request.form.get("status") or "").strip()
    if status and status not in SHELVES:
        abort(400)
    set_shelf(current_user.id, book.id, status or None)
    return redirect(safe_next_url(request.form.get("next")) or url_for("books.book_details", book_id=book.id))
