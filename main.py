from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, send_file, jsonify, abort
)
from flask_login import login_user, login_required, current_user, logout_user
from flask_wtf.csrf import CSRFError
from io import BytesIO
import qrcode
import qrcode.image.pil  # ensure PIL backend available
import os
import csv
import click
from datetime import datetime
from sqlalchemy import func

from app.config import get_config
from app.extensions import csrf, db, login_manager, migrate
from app.models import Book, Favourite, Follow, History, Review, User
from app.services import openlibrary
from app.services.avatars import delete_avatar_file, save_avatar
from app.services.catalogue import (
    GENRES, PLACEHOLDER_COVER, SORT_OPTIONS,
    average_rating, book_dict, books_with_ratings, paginate_books, ratings_for,
)
from app.services.security import (
    generate_reset_token, password_problem, rate_limited, safe_cover_url,
    safe_next_url, verify_reset_token,
)
from app.services.users import (
    HANDLE_RULES, display_name, follow_counts, handle_taken, is_following,
    normalize_handle, toggle_follow,
)


# -----------------------------------------------------------------------------
# App & DB
# -----------------------------------------------------------------------------
# config, extensions, models and services are in the app/ folder
app = Flask(__name__)

config_class = get_config()
app.config.from_object(config_class)
config_class.init_app(app)
os.makedirs(app.instance_path, exist_ok=True)

db.init_app(app)
migrate.init_app(app, db)
csrf.init_app(app)
login_manager.init_app(app)


@app.errorhandler(CSRFError)
def handle_csrf_error(error: CSRFError):
    """Explain a missing or expired token instead of showing a bare 400 page."""
    message = "Your session expired or the form was out of date. Please try again."
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"ok": False, "error": message}), 400
    flash(message, "error")
    if request.endpoint == "logout":
        return redirect(url_for("home"))
    return redirect(request.path)


# The database schema is now managed by Flask-Migrate (Alembic).
# Create or update tables with:   flask --app main db upgrade
# After changing a model, record it: flask --app main db migrate -m "what changed"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def current_user_name() -> str:
    if current_user.is_authenticated and current_user.name:
        return current_user.name
    return ""


def current_user_handle() -> str:
    if current_user.is_authenticated and current_user.handle:
        return current_user.handle
    return ""


def _page_number() -> int:
    try:
        return int(request.args.get("page", 1))
    except ValueError:
        return 1


def _time_ago(dt: datetime) -> str:
    """Return a friendly 'time ago' string like 2d ago, 3h ago."""
    if not dt:
        return ""
    now = datetime.utcnow()
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


# -----------------------------------------------------------------------------
# Seeding the catalogue
# -----------------------------------------------------------------------------
SEED_FILE = os.path.join(app.root_path, "data", "books.csv")


def _is_referenced(book_id: int) -> bool:
    """True if any review, favourite or history entry points at this book."""
    return any(
        model.query.filter_by(book_id=book_id).first() is not None
        for model in (Review, Favourite, History)
    )


def _apply_metadata(book: Book, meta: dict) -> None:
    """Copy Open Library details onto a book (keeping a year it already has)."""
    book.year = book.year or (meta.get("year") or "").strip()
    book.cover_i = (meta.get("cover_i") or "").strip()
    book.isbn = (meta.get("isbn") or "").strip()
    book.olid = (meta.get("olid") or "").strip()
    book.cover = safe_cover_url(meta.get("cover")) or PLACEHOLDER_COVER


def _metadata_for(title: str, author: str, cover_id: str = "") -> dict:
    """Open Library details, with a hand-picked cover id from books.csv winning if given."""
    meta = openlibrary.best_match(title, author) or {}
    if cover_id:
        meta["cover_i"] = cover_id
        meta["cover"] = openlibrary.cover_url(cover_i=cover_id)
    return meta


@app.cli.command("seed")
@click.option("--reset", is_flag=True,
              help="Also delete books that aren't in data/books.csv and that nobody has used.")
@click.option("--refresh-covers", is_flag=True,
              help="Look up covers again for curated books that are already saved.")
def seed_command(reset: bool, refresh_covers: bool) -> None:
    """Load the curated books in data/books.csv. Needs an internet connection.

    Run with:  flask --app main seed                   (add missing books)
               flask --app main seed --reset           (also clear out other unused books)
               flask --app main seed --refresh-covers  (re-fetch covers for saved books)

    books.csv has an optional cover_id column: put an Open Library cover id
    there to choose a book's cover by hand when the automatic match is wrong.
    """
    with open(SEED_FILE, newline="", encoding="utf-8") as f:
        curated = [
            (r["title"].strip(), r["author"].strip(), r["genre"].strip(), (r.get("cover_id") or "").strip())
            for r in csv.DictReader(f)
        ]
    curated_keys = {(title, author) for title, author, _, _ in curated}

    if reset:
        unused = [
            b for b in Book.query.all()
            if (b.title, b.author) not in curated_keys and not _is_referenced(b.id)
        ]
        click.confirm(f"Delete {len(unused)} books that aren't curated and nobody has used?", abort=True)
        for b in unused:
            db.session.delete(b)
        db.session.commit()
        click.echo(f"Deleted {len(unused)} unused books.")

    added = updated = refreshed = 0
    for i, (title, author, genre, cover_id) in enumerate(curated, start=1):
        book = Book.query.filter_by(title=title, author=author).first()

        if book is None:
            book = Book(title=title, author=author, genre=genre)
            _apply_metadata(book, _metadata_for(title, author, cover_id))
            db.session.add(book)
            added += 1
            click.echo(f"[{i}/{len(curated)}] added {title}")
        else:
            if book.genre != genre:
                book.genre = genre  # fix genres from the old seeding
                updated += 1
            if refresh_covers or (cover_id and book.cover_i != cover_id):
                _apply_metadata(book, _metadata_for(title, author, cover_id))
                refreshed += 1
                click.echo(f"[{i}/{len(curated)}] new cover for {title}")
        db.session.commit()  # save as we go, so a dropped connection loses nothing

    click.echo(f"Added {added} books, fixed the genre of {updated}, refreshed {refreshed} covers. "
               f"The catalogue now has {Book.query.count()} books.")


# -----------------------------------------------------------------------------
# Routes - Core Screens
# -----------------------------------------------------------------------------
@app.route("/")
def home():
    # Only books people have actually rated appear under "Top Rated".
    rows = db.session.execute(books_with_ratings(rated_only=True).limit(6)).all()
    top_rated = [book_dict(bk, float(avg), count) for bk, avg, count in rows]
    first_book = db.session.scalar(db.select(Book).order_by(Book.title).limit(1))
    featured = top_rated[0] if top_rated else (book_dict(first_book, 0.0) if first_book else {})
    return render_template(
        "index.html",
        featured=featured,
        genres=GENRES,
        top_rated=top_rated,
    )


@app.route("/browse")
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


@app.route("/book-details/<path:title>")
def book_details_by_title(title):
    """Old title-based links still work: send them to the book's new address."""
    bk = (
        Book.query.filter_by(title=title).order_by(Book.id).first()
        or Book.query.filter(func.lower(Book.title) == title.lower()).order_by(Book.id).first()
    )
    if not bk:
        abort(404)
    return redirect(url_for("book_details", book_id=bk.id), code=301)


@app.route("/books/<int:book_id>")
def book_details(book_id):
    bk = db.get_or_404(Book, book_id)

    latest_reviews = (
        Review.query
        .filter_by(book_id=bk.id)
        .order_by(Review.created.desc())
        .limit(25)
        .all()
    )
    viewer_id = current_user.id if current_user.is_authenticated else None
    reviews_for_template = [
        {
            "id": r.id,
            "user": display_name(r.user),
            "rating": r.rating,
            "text": (r.text or "").strip(),
            "age": _time_ago(r.created),
            "is_mine": r.user_id == viewer_id,
        }
        for r in latest_reviews
    ]
    my_review = (
        Review.query.filter_by(user_id=viewer_id, book_id=bk.id).first() if viewer_id else None
    )

    # Fetched from Open Library once, then kept in the database.
    # Shown only when Open Library has one (the old made-up fallback text is gone).
    if bk.description is None:
        fetched = openlibrary.description_for(bk.title, bk.author)
        if fetched is not None:          # None means Open Library couldn't be reached
            bk.description = fetched     # may be "" if it has no description
            db.session.commit()
    description = bk.description or ""

    return render_template(
        "book_details.html",
        book=book_dict(bk, average_rating(bk.id)),
        book_id=bk.id,
        description=description,
        reviews=reviews_for_template,
        my_review_id=my_review.id if my_review else None,
    )


@app.route("/books/<int:book_id>/reviews")
def book_reviews(book_id):
    bk = db.get_or_404(Book, book_id)
    rows = Review.query.filter_by(book_id=bk.id).order_by(Review.created.desc()).all()
    reviews = [
        {
            "user": display_name(r.user),
            "rating": r.rating,
            "text": (r.text or "").strip(),
            "created": r.created,
        }
        for r in rows
    ]
    return render_template(
        "book_reviews.html",
        book=book_dict(bk, average_rating(bk.id)),
        reviews=reviews,
        avg_rating=average_rating(bk.id),
        review_count=len(rows),
    )


def _parse_rating(raw) -> int | None:
    """Turn the submitted rating into 1-5, or None if it isn't a valid rating."""
    try:
        rating = int(raw)
    except (TypeError, ValueError):
        return None
    return rating if 1 <= rating <= 5 else None


@app.route("/add-review", methods=["GET", "POST"])
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
            return redirect(url_for("add_review"))
        if rating is None:
            flash("Choose a rating from 1 to 5 stars.", "error")
            return redirect(url_for("add_review"))
        if Review.query.filter_by(user_id=current_user.id, book_id=bk.id).first():
            flash("You've already reviewed this book.", "error")
            return redirect(url_for("book_details", book_id=bk.id))

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
        return redirect(url_for("book_details", book_id=bk.id))

    return render_template("add_review.html")


def _own_review_or_403(review_id: int) -> Review:
    """The review, but only if the logged-in user wrote it.

    Checking ownership on the server matters: hiding the Edit button isn't
    enough, because anyone can send a request with a different review id.
    """
    review = db.get_or_404(Review, review_id)
    if review.user_id != current_user.id:
        abort(403)
    return review


@app.route("/reviews/<int:review_id>/edit", methods=["GET", "POST"])
@login_required
def edit_review(review_id):
    review = _own_review_or_403(review_id)
    if request.method == "POST":
        rating = _parse_rating(request.form.get("rating"))
        text = (request.form.get("review") or "").strip()
        if rating is None or not text:
            flash("Choose a rating from 1 to 5 stars and write something.", "error")
            return redirect(url_for("edit_review", review_id=review.id))
        review.rating = rating
        review.text = text
        db.session.commit()
        flash("Review updated.", "ok")
        return redirect(url_for("book_details", book_id=review.book_id))
    return render_template("edit_review.html", review=review, book=book_dict(review.book, 0.0))


@app.post("/reviews/<int:review_id>/delete")
@login_required
def delete_review(review_id):
    review = _own_review_or_403(review_id)
    book_id = review.book_id
    db.session.delete(review)
    db.session.commit()
    flash("Review deleted.", "ok")
    return redirect(url_for("book_details", book_id=book_id))


@app.route("/add-book", methods=["GET", "POST"])
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
            return redirect(url_for("add_book"))

        existing = Book.query.filter_by(title=title, author=author).first()
        if existing:
            flash("That book already exists.", "error")
            return redirect(url_for("add_review"))

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
        return redirect(url_for("add_review"))

    return render_template("add_book.html", genres=GENRES)


@app.route("/favourites")
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


@app.post("/api/favourites/toggle")
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

@app.get("/api/favourites")
@login_required
def api_favourites_list():
    ids = [f.book_id for f in Favourite.query.filter_by(user_id=current_user.id).all()]
    return jsonify({"ok": True, "book_ids": ids})

@app.route("/history")
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


@app.post("/api/history/add")
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



@app.route("/top-rated")
def top_rated():
    q = (request.args.get("q") or "").strip()
    results = paginate_books(books_with_ratings(q=q, rated_only=True), _page_number())
    return render_template("top_rated.html", books=results.items, results=results)



# ----------------------------
# SOCIAL: Following / Followers
# ----------------------------
def _people(users, q: str) -> list[dict]:
    """The shape the Followers and Following pages expect, filtered by an optional search."""
    q = (q or "").strip().lower()
    people = []
    for u in users:
        name, handle = (u.name or ""), (u.handle or "")
        if q and q not in name.lower() and q not in handle.lower():
            continue
        people.append({"id": u.id, "name": name or "Reader", "handle": handle, "avatar": u.avatar or ""})
    return people


@app.route("/following")
@login_required
def following():
    users = (
        db.session.execute(
            db.select(User).join(Follow, Follow.followed_id == User.id)
            .where(Follow.follower_id == current_user.id)
            .order_by(Follow.created_at.desc())
        ).scalars().all()
    )
    return render_template("following.html", people=_people(users, request.args.get("q")))


@app.route("/followers")
@login_required
def followers():
    users = (
        db.session.execute(
            db.select(User).join(Follow, Follow.follower_id == User.id)
            .where(Follow.followed_id == current_user.id)
            .order_by(Follow.created_at.desc())
        ).scalars().all()
    )
    return render_template("followers.html", people=_people(users, request.args.get("q")))


def _target_user_from_json():
    """The user named by {"user_id": ...} in a JSON request, or None."""
    data = request.get_json(silent=True) or {}
    try:
        return db.session.get(User, int(data.get("user_id") or 0))
    except (TypeError, ValueError):
        return None


@app.post("/api/following/toggle")
@login_required
def api_following_toggle():
    target = _target_user_from_json()
    if target is None:
        return jsonify({"ok": False, "error": "That reader doesn't exist."}), 404
    if target.id == current_user.id:
        return jsonify({"ok": False, "error": "You can't follow yourself."}), 400
    now_following = toggle_follow(current_user, target)
    return jsonify({"ok": True, "state": "followed" if now_following else "unfollowed"})


@app.post("/api/followers/remove")
@login_required
def api_followers_remove():
    target = _target_user_from_json()
    row = target and Follow.query.filter_by(follower_id=target.id, followed_id=current_user.id).first()
    if not row:
        return jsonify({"ok": False, "error": "That reader doesn't follow you."}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@app.post("/users/<int:user_id>/follow")
@login_required
def follow_toggle_form(user_id):
    """The Follow / Unfollow button on a public profile (works without JavaScript)."""
    target = db.get_or_404(User, user_id)
    if target.id != current_user.id:
        toggle_follow(current_user, target)
    if target.handle:
        return redirect(url_for("public_profile", handle=target.handle.lstrip("@")))
    return redirect(url_for("profile_page"))


# ---------- Genre landing  ----------
@app.route("/genre/<name>")
def genre_page(name):
    return redirect(url_for("browse", genre=name), code=302)


# -----------------------------------------------------------------------------
# Open Library API (used by UI search buttons)
# -----------------------------------------------------------------------------
@app.get("/api/openlibrary")
def api_openlibrary():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    return jsonify(openlibrary.search(q))


# -----------------------------------------------------------------------------
# Profile + Settings
# -----------------------------------------------------------------------------
@app.route("/profile")
@login_required
def profile_page():
    user = {
        "name": current_user.name or "",
        "handle": current_user.handle or "",
        "bio": current_user.bio or "",
        "avatar": current_user.avatar or "",
    }

    recent = (
        Review.query
        .filter_by(user_id=current_user.id)
        .order_by(Review.created.desc())
        .limit(6)
        .all()
    )

    followers_count, following_count = follow_counts(current_user.id)
    return render_template(
        "profile.html", user=user, your_reviews=recent, is_own=True,
        review_count=Review.query.filter_by(user_id=current_user.id).count(),
        followers_count=followers_count, following_count=following_count,
    )


@app.route("/my-reviews")
@login_required
def my_reviews():
    reviews = (
        Review.query
        .filter_by(user_id=current_user.id)
        .order_by(Review.created.desc())
        .all()
    )
    return render_template("my_reviews.html", reviews=reviews)

@app.errorhandler(413)
def file_too_large(error):
    flash("That picture is too big. Please choose one under 2 MB.", "error")
    return redirect(url_for("edit_profile"))


@app.route("/edit-profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        # ----------------------------
        # Text fields
        # ----------------------------
        new_name = (request.form.get("name") or "").strip()
        new_handle = (request.form.get("handle") or "").strip()
        new_bio = (request.form.get("bio") or "").strip()

        if new_name:
            current_user.name = new_name

        if new_handle:
            handle = normalize_handle(new_handle)
            if handle is None:
                flash(HANDLE_RULES, "error")
                return redirect(url_for("edit_profile"))
            if handle_taken(handle, except_user_id=current_user.id):
                flash(f"{handle} is already taken. Try another handle.", "error")
                return redirect(url_for("edit_profile"))
            current_user.handle = handle

        current_user.bio = new_bio[:200]

        # ----------------------------
        # Profile picture upload
        # ----------------------------
        file = request.files.get("avatar")
        if file and file.filename:
            new_path = save_avatar(file, current_user.id)
            if new_path is None:
                flash("That file isn't a picture we can use. Try a JPG, PNG or WebP image.", "error")
                return redirect(url_for("edit_profile"))
            delete_avatar_file(current_user.avatar)
            current_user.avatar = new_path

        db.session.commit()
        flash("Profile updated.", "ok")
        return redirect(url_for("profile_page"))

    # pass the actual current_user so templates can read user.avatar, user.name etc
    return render_template("edit_profile.html", user=current_user)


@app.route("/share-profile")
def share_profile():
    user = {"name": current_user_name(), "handle": current_user_handle()}
    books = []
    if current_user.is_authenticated:
        reviewed = (
            Review.query.filter_by(user_id=current_user.id)
            .order_by(Review.created.desc()).limit(10).all()
        )
        books = [book_dict(r.book, average_rating(r.book_id)) for r in reviewed]
    return render_template("share_profile.html", user=user, books=books)


@app.route("/qr-profile")
def qr_profile():
    # Only make QR codes for links to this site, so the endpoint can't be
    # used to generate QR codes pointing anywhere on the internet.
    url = request.args.get("url") or ""
    if not url.startswith(request.host_url):
        handle = current_user_handle().lstrip("@")
        if not handle:
            abort(404)
        url = url_for("public_profile", handle=handle, _external=True)

    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/public/<handle>")
def public_profile(handle):
    owner = User.query.filter_by(handle=f"@{handle.lstrip('@').lower()}").first_or_404()
    user = {
        "name": owner.name or "", "handle": owner.handle or "",
        "bio": owner.bio or "", "avatar": owner.avatar or "",
    }
    reviews = (
        Review.query.filter_by(user_id=owner.id)
        .order_by(Review.created.desc()).limit(6).all()
    )
    followers_count, following_count = follow_counts(owner.id)
    is_own = current_user.is_authenticated and current_user.id == owner.id
    hidden = owner.is_private and not is_own
    if hidden:
        reviews = []
    return render_template(
        "profile.html", user=user, your_reviews=reviews, is_private_view=hidden,
        profile_id=owner.id, is_own=is_own,
        is_following=current_user.is_authenticated and not is_own and is_following(current_user.id, owner.id),
        review_count=Review.query.filter_by(user_id=owner.id).count(),
        followers_count=followers_count, following_count=following_count,
    )


@app.route("/settings")
@login_required
def settings():
    user = {"name": current_user.name or "", "handle": current_user.handle or ""}
    return render_template("settings.html", user=user)


# ----------------------------
# Settings pages
# ----------------------------
@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current") or ""
        new = request.form.get("new") or ""
        confirm = request.form.get("confirm") or ""
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("change_password"))
        if password_problem(new):
            flash(password_problem(new), "error")
            return redirect(url_for("change_password"))
        if new != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("change_password"))

        current_user.set_password(new)
        db.session.commit()
        flash("Password updated.", "ok")
        return redirect(url_for("settings"))
    return render_template("change_password.html")


@app.route("/privacy", methods=["GET", "POST"])
@login_required
def privacy_settings():
    if request.method == "POST":
        current_user.is_private = bool(request.form.get("profile_private"))
        db.session.commit()
        flash("Privacy settings saved.", "ok")
        return redirect(url_for("privacy_settings"))
    return render_template("privacy_settings.html", profile_private=current_user.is_private)


# -----------------------------------------------------------------------------
# API for live search suggestions (used by Add Review typing UX)
# -----------------------------------------------------------------------------
@app.get("/api/search")
def api_search_books():
    """Live search for the Add Review page: up to 8 matches, straight from the database."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    rows = db.session.execute(books_with_ratings(q=q, sort="title").limit(8)).all()
    return jsonify([book_dict(bk, float(avg), count) for bk, avg, count in rows])


# -----------------------------------------------------------------------------
# Auth: Sign Up / Sign In / Sign Out
# -----------------------------------------------------------------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "")
        name = (request.form.get("name") or "").strip() or "New Reader"
        handle_raw = (request.form.get("handle") or "").strip()
        handle = normalize_handle(handle_raw) if handle_raw else None

        if not email or not password:
            flash("Email and password are required.", "error")
            return redirect(url_for("signup"))
        if password_problem(password):
            flash(password_problem(password), "error")
            return redirect(url_for("signup"))
        if handle_raw and handle is None:
            flash(HANDLE_RULES, "error")
            return redirect(url_for("signup"))
        if handle and handle_taken(handle):
            flash(f"{handle} is already taken. Try another handle.", "error")
            return redirect(url_for("signup"))

        if User.query.filter_by(email=email).first():
            flash("This email is already registered. Try logging in.", "error")
            return redirect(url_for("login"))

        user = User(email=email, name=name, handle=handle)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for("home"))

    return render_template("sign_up.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if rate_limited("login", limit=5, window=60):
            flash("Too many sign-in attempts. Please wait a minute and try again.", "error")
            return render_template("sign_in.html"), 429

        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "")
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login", next=request.args.get("next")))

        # Stay signed in for 30 days only if the reader ticked "Remember me".
        login_user(user, remember=bool(request.form.get("remember")))
        return redirect(safe_next_url(request.args.get("next")) or url_for("home"))
    return render_template("sign_in.html")


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        if rate_limited("forgot-password", limit=3, window=600):
            flash("Too many reset requests. Please try again in a few minutes.", "error")
            return render_template("forgot_password.html"), 429

        email = (request.form.get("email") or "").strip().lower()

        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_reset_token(user)
            reset_link = url_for("reset_password", token=token, _external=True)

            
            # Development only: email isn't set up yet, so the link goes to the log.
            app.logger.warning("PASSWORD RESET LINK for %s: %s", user.email, reset_link)

        flash("If that email exists, you’ll receive a password reset link shortly.", "ok")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = verify_reset_token(token, max_age_seconds=60 * 60)
    if user is None:
        flash("That reset link is invalid, expired or already used. Please request a new one.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_pw = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if password_problem(new_pw):
            flash(password_problem(new_pw), "error")
            return redirect(url_for("reset_password", token=token))
        if new_pw != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("reset_password", token=token))

        user.set_password(new_pw)
        db.session.commit()
        flash("Password updated. You can sign in now.", "ok")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)

@app.context_processor
def inject_globals():
    return {"PLACEHOLDER_COVER": PLACEHOLDER_COVER}


# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Debug mode shows an interactive console in the browser when an error
    # happens, so it is only switched on when FLASK_DEBUG=1 is set locally.
    # The usual way to run locally is:  flask --app main run --debug
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
