"""Writing, editing and deleting reviews."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Book, Review
from app.services.catalogue import book_dict

bp = Blueprint("reviews", __name__)


def _parse_rating(raw: str | None) -> int | None:
    """Rating as an int from 1-5, or None if it's invalid."""
    try:
        rating = int(raw)
    except (TypeError, ValueError):
        return None
    return rating if 1 <= rating <= 5 else None


def _own_review_or_403(review_id: int) -> Review:
    """Get a review, or 403 if it belongs to someone else.

    Hiding the edit button isn't enough on its own - anyone could send a
    request with a different review id, so this is checked on the server.
    """
    review = db.get_or_404(Review, review_id)
    if review.user_id != current_user.id:
        abort(403)
    return review


@bp.route("/add-review", methods=["GET", "POST"])
@login_required
def add_review():
    if request.method == "POST":
        review_text = (request.form.get("review") or "").strip()
        rating = _parse_rating(request.form.get("rating"))
        try:
            book_id = int(request.form.get("book_id") or 0)
        except ValueError:
            book_id = 0
        bk = db.session.get(Book, book_id) if book_id else None

        if not bk or not review_text:
            flash("Pick a book and write a review first.", "error")
            return redirect(url_for("reviews.add_review"))
        if rating is None:
            flash("Choose a rating from 1 to 5 stars.", "error")
            return redirect(url_for("reviews.add_review"))
        if Review.query.filter_by(user_id=current_user.id, book_id=bk.id).first():
            flash("You've already reviewed this book.", "error")
            return redirect(url_for("books.book_details", book_id=bk.id))

        cover = book_dict(bk, 0.0)["cover"]
        db.session.add(Review(
            user_id=current_user.id,
            book_id=bk.id,
            book_title=bk.title,
            book_author=bk.author,
            book_cover=cover,
            rating=rating,
            text=review_text,
        ))
        db.session.commit()

        flash("Thanks for your review!", "ok")
        return redirect(url_for("books.book_details", book_id=bk.id))

    return render_template("add_review.html")


@bp.route("/reviews/<int:review_id>/edit", methods=["GET", "POST"])
@login_required
def edit_review(review_id):
    review = _own_review_or_403(review_id)
    if request.method == "POST":
        rating = _parse_rating(request.form.get("rating"))
        text = (request.form.get("review") or "").strip()
        if rating is None or not text:
            flash("Choose a rating from 1 to 5 stars and write something.", "error")
            return redirect(url_for("reviews.edit_review", review_id=review.id))
        review.rating = rating
        review.text = text
        db.session.commit()
        flash("Review updated.", "ok")
        return redirect(url_for("books.book_details", book_id=review.book_id))
    return render_template("edit_review.html", review=review, book=book_dict(review.book, 0.0))


@bp.post("/reviews/<int:review_id>/delete")
@login_required
def delete_review(review_id):
    review = _own_review_or_403(review_id)
    book_id = review.book_id
    db.session.delete(review)
    db.session.commit()
    flash("Review deleted.", "ok")
    return redirect(url_for("books.book_details", book_id=book_id))


@bp.route("/my-reviews")
@login_required
def my_reviews():
    reviews = (
        Review.query
        .filter_by(user_id=current_user.id)
        .order_by(Review.created.desc())
        .all()
    )
    return render_template("my_reviews.html", reviews=reviews)
