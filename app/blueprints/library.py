"""The reader's own lists - favourites and recently viewed."""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Book, Favourite, History
from app.services.catalogue import PLACEHOLDER_COVER, ratings_for

bp = Blueprint("library", __name__)


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
    books = []
    for b in rows:
        if q and (q not in (b.title or "").lower()) and (q not in (b.author or "").lower()):
            continue
        books.append({
            "id": b.id,
            "title": b.title,
            "author": b.author,
            "cover": b.cover or PLACEHOLDER_COVER,
            "rating": ratings.get(b.id, (0.0, 0))[0],
            "review_count": ratings.get(b.id, (0.0, 0))[1],
        })

    return render_template("favourites.html", books=books, q=q)


@bp.post("/api/favourites/toggle")
@login_required
def api_favourites_toggle():
    data = request.get_json(silent=True) or {}
    book_id = int(data.get("book_id") or 0)
    if not book_id:
        return jsonify({"ok": False, "error": "missing book_id"}), 400

    fav = Favourite.query.filter_by(user_id=current_user.id, book_id=book_id).first()
    if fav:
        db.session.delete(fav)
        db.session.commit()
        return jsonify({"ok": True, "favourited": False})

    db.session.add(Favourite(user_id=current_user.id, book_id=book_id))
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
    books = []
    for b, viewed_at in rows:
        if q and (q not in (b.title or "").lower()) and (q not in (b.author or "").lower()):
            continue
        books.append({
            "id": b.id,
            "title": b.title,
            "author": b.author,
            "cover": b.cover or PLACEHOLDER_COVER,
            "viewed_at": viewed_at,
            "rating": ratings.get(b.id, (0.0, 0))[0],
            "review_count": ratings.get(b.id, (0.0, 0))[1],
        })

    return render_template("history.html", books=books, q=q)


@bp.post("/api/history/add")
@login_required
def api_history_add():
    data = request.get_json(silent=True) or {}
    book_id = int(data.get("book_id") or 0)
    if not book_id:
        return jsonify({"ok": False, "error": "missing book_id"}), 400

    row = History.query.filter_by(user_id=current_user.id, book_id=book_id).first()
    if row:
        row.viewed_at = datetime.utcnow()
    else:
        db.session.add(History(user_id=current_user.id, book_id=book_id))

    db.session.commit()
    return jsonify({"ok": True})
