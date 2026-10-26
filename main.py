from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, send_file, jsonify, session, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    current_user, logout_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect, CSRFError
from io import BytesIO
import qrcode
import qrcode.image.pil  # ensure PIL backend available
import os
import re
import secrets
import sqlite3
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import event, func, or_
from sqlalchemy.engine import Engine
from urllib.parse import quote, urlparse
from urllib.request import urlopen, Request
import json
from dataclasses import dataclass
import csv
import click


# -----------------------------------------------------------------------------
# App & DB
# -----------------------------------------------------------------------------
# Read settings from a .env file in the project folder, if there is one.
# Real environment variables always win over values in .env.
load_dotenv()

app = Flask(__name__)

# "development" (default), "testing" or "production"
APP_ENV = os.environ.get("BOOKBUDDY_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"


def _load_secret_key() -> str:
    """Return the key Flask uses to sign session cookies and reset tokens.

    Anyone who knows this key can forge a login cookie, so it must never be
    a fixed value in the code. In production the app refuses to start
    without one.
    """
    key = (os.environ.get("SECRET_KEY") or os.environ.get("BOOKBUDDY_SECRET") or "").strip()
    if key:
        return key
    if IS_PRODUCTION:
        raise RuntimeError("SECRET_KEY must be set when BOOKBUDDY_ENV=production.")
    app.logger.warning(
        "SECRET_KEY is not set, so a temporary random key is being used and "
        "everyone is logged out when the server restarts. Set SECRET_KEY in .env."
    )
    return secrets.token_hex(32)


app.config["SECRET_KEY"] = _load_secret_key()

os.makedirs(app.instance_path, exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.environ.get("DATABASE_URL")
    or "sqlite:///" + os.path.join(app.instance_path, "bookbuddy.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
# render_as_batch lets Alembic change columns on SQLite, which can't ALTER
# most things in place: it copies the table, changes it and swaps it back.
migrate = Migrate(app, db, render_as_batch=True)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """SQLite ignores foreign keys unless told otherwise on every connection.

    With this on, the database itself refuses a review for a book or user
    that doesn't exist. PostgreSQL always enforces them, so this only
    matters locally.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# CSRF protection: every POST must carry a secret token that only pages from
# this site know, so another website can't submit forms as a logged-in user.
# Forms send it as a hidden "csrf_token" field; fetch() calls send it in an
# "X-CSRFToken" header (see base.html).
csrf = CSRFProtect(app)


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
login_manager = LoginManager(app)
login_manager.login_view = "login"


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), default="")
    # Stored like "@alexmorgan": lower case, unique, or None if not chosen yet.
    handle = db.Column(db.String(120), nullable=True, default=None)
    bio = db.Column(db.Text, default="")
    # Path inside /static, e.g. "uploads/avatars/user_4_photo.jpg", or "" for none.
    avatar = db.Column(db.String(255), default="")

    # A unique index (not a constraint) so SQLite can add it without rebuilding the table.
    __table_args__ = (db.Index("uq_user_handle", "handle", unique=True),)

    def set_password(self, pw: str) -> None:
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    user = db.relationship("User", backref=db.backref("reviews", lazy=True))

    # Which book this review is about. Before Phase 3 task 4, reviews were
    # matched to books by title text, so books with the same title shared reviews.
    book_id = db.Column(
        db.Integer, db.ForeignKey("book.id", name="fk_review_book_id_book"),
        nullable=False, index=True,
    )
    book = db.relationship("Book", backref=db.backref("reviews", lazy=True))

    # Copies of the book's details at the time of the review.
    # Task 5 removes these and reads them from review.book instead.
    book_title = db.Column(db.String(255), nullable=False)
    book_author = db.Column(db.String(255), default="")
    book_cover = db.Column(db.String(500), default="")

    rating = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, default="")
    created = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        # One review per reader per book.
        db.UniqueConstraint("user_id", "book_id", name="uq_review_user_book"),
        # The database rejects any rating outside 1 to 5, even if a bug lets one through.
        db.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating_range"),
    )


class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False, index=True)
    author = db.Column(db.String(255), nullable=False, index=True)
    genre = db.Column(db.String(120), default="Other")
    year = db.Column(db.String(20), default="")
    cover = db.Column(db.String(500), default="")

    # Open Library identifiers 
    olid = db.Column(db.String(50), default="", index=True)     
    cover_i = db.Column(db.String(50), default="")              
    isbn = db.Column(db.String(32), default="")                 

    # Fetched from Open Library the first time someone opens the book page.
    # None = not fetched yet, "" = Open Library has no description.
    description = db.Column(db.Text, nullable=True)

    __table_args__ = (db.UniqueConstraint("title", "author", name="uq_book_title_author"),)

class Favourite(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "book_id", name="uq_favourite_user_book"),)


class Follow(db.Model):
    """One reader following another. follower -> followed."""
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    followed_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    follower = db.relationship("User", foreign_keys=[follower_id])
    followed = db.relationship("User", foreign_keys=[followed_id])

    __table_args__ = (
        # You can follow someone once...
        db.UniqueConstraint("follower_id", "followed_id", name="uq_follow_pair"),
        # ...and never yourself, even if a bug in the app tried to.
        db.CheckConstraint("follower_id != followed_id", name="ck_follow_not_self"),
    )


class History(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False, index=True)

    viewed_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (db.UniqueConstraint("user_id", "book_id", name="uq_history_user_book"),)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# The database schema is now managed by Flask-Migrate (Alembic).
# Create or update tables with:   flask --app main db upgrade
# After changing a model, record it: flask --app main db migrate -m "what changed"


# -----------------------------------------------------------------------------
# Demo Data 
# -----------------------------------------------------------------------------
# The 9 hard-coded demo books that used to live here were moved into the
# database by the migration in Phase 3 task 4.

GENRES = [
    "Romance", "Fantasy", "Horror", "Mystery", "Non-fiction",
    "Sci-Fi", "Classics", "Dystopian", "Gothic", "Literary", "Adventure"
]

# ----------------------------
# App/session preferences demo
# ----------------------------
LANGS = ["English", "Español", "Français", "Deutsch", "Italiano"]


def get_pref(key, default=None):
    return session.get(key, default)


def set_pref(key, value):
    session[key] = value


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
HANDLE_PATTERN = re.compile(r"[a-z0-9_.]{3,30}")


def normalize_handle(raw: str) -> str | None:
    """Turn what someone typed into a stored handle, e.g. " Alex.Morgan " -> "@alex.morgan".

    Returns None if it isn't valid: 3-30 characters, only letters,
    numbers, dots and underscores.
    """
    body = (raw or "").strip().lstrip("@").lower()
    return f"@{body}" if HANDLE_PATTERN.fullmatch(body) else None


def handle_taken(handle: str, except_user_id: int | None = None) -> bool:
    """True if another account already uses this handle."""
    query = User.query.filter_by(handle=handle)
    if except_user_id is not None:
        query = query.filter(User.id != except_user_id)
    return query.first() is not None


HANDLE_RULES = "Handles are 3-30 characters: letters, numbers, dots and underscores."


def current_user_name() -> str:
    if current_user.is_authenticated and current_user.name:
        return current_user.name
    return ""


def current_user_handle() -> str:
    if current_user.is_authenticated and current_user.handle:
        return current_user.handle
    return ""


def _reset_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="password-reset")


def generate_reset_token(email: str) -> str:
    return _reset_serializer().dumps(email)


def verify_reset_token(token: str, max_age_seconds: int = 3600):
    try:
        return _reset_serializer().loads(token, max_age=max_age_seconds)
    except (SignatureExpired, BadSignature):
        return None


PLACEHOLDER_COVER = "https://placehold.co/400x600/EEE/AAA?text=No+Cover"


def safe_cover_url(url: str) -> str:
    """Return the URL only if it is a plain https:// web address, else "".

    Cover links can come from users, so anything else (javascript:, data:,
    text with quotes or angle brackets) is thrown away before it is saved
    or shown.
    """
    url = (url or "").strip()
    if not url or any(ch in url for ch in "\"'<> "):
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return url


def _ol_cover_url(cover_i: str = "", isbn: str = "", olid: str = "") -> str:
    """Build an Open Library cover URL from the best identifier we have.

    Priority: cover id, then ISBN, then edition id. "?default=false" makes
    Open Library return an error instead of a blank 1x1 image when it has no
    cover, so the page can show the title-and-author tile instead.
    """
    cover_i = (cover_i or "").strip()
    isbn = (isbn or "").strip()
    olid = (olid or "").strip()

    if cover_i:
        return f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg?default=false"
    if isbn:
        return f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
    if olid:
        return f"https://covers.openlibrary.org/b/olid/{olid}-L.jpg?default=false"
    return ""


def _ol_get_json(url: str, timeout: int = 15):
    """GET a JSON document from Open Library, or None if anything goes wrong."""
    req = Request(url, headers={"User-Agent": "BookBuddy/1.0 (personal project)"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _score_search_result(doc: dict, title: str, author: str) -> int:
    """How well one Open Library search result matches the book we want.

    Title and author matter most. After that, prefer English-language works
    and works with many editions, which are usually the well-known version
    rather than a translation or an obscure reprint.
    """
    t_low, a_low = title.strip().lower(), author.strip().lower()
    score = 0

    d_title = (doc.get("title") or "").strip().lower()
    if d_title == t_low:
        score += 50
    elif t_low and t_low in d_title:
        score += 25

    authors = doc.get("author_name") or []
    first_author = (authors[0] or "").strip().lower() if authors else ""
    if a_low and first_author:
        if first_author == a_low:
            score += 50
        elif a_low in first_author or first_author in a_low:
            score += 25

    if doc.get("cover_i"):
        score += 10
    if "eng" in (doc.get("language") or []):
        score += 20
    score += min(int(doc.get("edition_count") or 0), 100) // 5   # up to +20

    return score


def pick_best_search_result(docs: list, title: str, author: str = "") -> dict | None:
    """The search result that best matches (title, author), or None if there are none."""
    if not docs:
        return None
    return max(docs, key=lambda doc: _score_search_result(doc, title, author))


def _english_edition_cover(work_key: str) -> str:
    """The cover id of an English edition of this work, or "" if there isn't one.

    A work's default cover is sometimes a translation's, e.g. a French
    "Une brève histoire du temps" for A Brief History of Time.
    """
    if not (work_key or "").startswith("/works/"):
        return ""
    data = _ol_get_json(f"https://openlibrary.org{work_key}/editions.json?limit=100") or {}
    for edition in data.get("entries") or []:
        languages = {lang.get("key") for lang in edition.get("languages") or []}
        covers = [c for c in edition.get("covers") or [] if isinstance(c, int) and c > 0]
        if "/languages/eng" in languages and covers:
            return str(covers[0])
    return ""


def _ol_best_match(title: str, author: str = "", timeout: int = 15) -> dict:
    """Look a book up on Open Library.

    Returns {cover, cover_i, isbn, olid, year}, or {} if nothing was found
    or Open Library couldn't be reached.
    """
    t = (title or "").strip()
    a = (author or "").strip()
    if not t:
        return {}

    fields = "key,title,author_name,cover_i,isbn,edition_key,first_publish_year,language,edition_count"
    q = t if not a else f"{t} {a}"
    data = _ol_get_json(
        f"https://openlibrary.org/search.json?q={quote(q)}&limit=20&fields={fields}", timeout=timeout
    ) or {}
    best = pick_best_search_result(data.get("docs") or [], t, a)
    if best is None:
        return {}

    cover_i = _english_edition_cover(best.get("key") or "") or str(best.get("cover_i") or "").strip()
    olid = str((best.get("edition_key") or [""])[0] or "").strip()
    isbn = str((best.get("isbn") or [""])[0] or "").strip()
    year = str(best["first_publish_year"]) if isinstance(best.get("first_publish_year"), int) else ""

    cover = _ol_cover_url(cover_i=cover_i, isbn=isbn, olid=olid)
    return {"cover": cover, "cover_i": cover_i, "isbn": isbn, "olid": olid, "year": year}


BOOKS_PER_PAGE = 24
SORT_OPTIONS = {
    "rating": "Highest rated",
    "reviews": "Most reviewed",
    "title": "Title A–Z",
    "newest": "Newest added",
}


def books_with_ratings(q: str = "", genre: str = "", sort: str = "rating", rated_only: bool = False):
    """A database query for books plus each one's average rating and review count.

    Everything (searching, filtering, sorting) happens in the database,
    so only the books on the current page are ever loaded into Python.
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
        # autoescape: a % or _ typed by the user is searched for literally.
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
    """One page of results, with what the template needs for page links."""
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
    total = db.session.scalar(db.select(func.count()).select_from(stmt.order_by(None).subquery()))
    page = min(max(page, 1), max(1, -(-total // BOOKS_PER_PAGE)))
    rows = db.session.execute(stmt.limit(BOOKS_PER_PAGE).offset((page - 1) * BOOKS_PER_PAGE)).all()
    return BookPage(
        items=[_book_dict(bk, float(avg), count) for bk, avg, count in rows],
        page=page,
        total=total,
    )


def ratings_for(book_ids) -> dict:
    """{book id: (average rating, review count)} for the given books, in one query."""
    if not book_ids:
        return {}
    rows = db.session.execute(
        db.select(Review.book_id, func.avg(Review.rating), func.count(Review.id))
        .where(Review.book_id.in_(book_ids))
        .group_by(Review.book_id)
    ).all()
    return {book_id: (float(avg), count) for book_id, avg, count in rows}


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


def _ol_description_for(title: str, author: str = "", timeout: int = 4) -> str | None:
    """
    Best-effort description from Open Library.
    Uses search -> first work -> work details.
    Falls back to empty string if anything fails.
    """
    t = (title or "").strip()
    a = (author or "").strip()
    if not t:
        return ""

    q = t if not a else f"{t} {a}"
    search_url = f"https://openlibrary.org/search.json?q={quote(q)}&limit=5&page=1"
    req = Request(search_url, headers={"User-Agent": "BookBuddy/1.0 (personal project)"})

    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None  # network problem: try again next time

    docs = data.get("docs") or []
    if not docs:
        return ""

    # pick the most relevant doc (basic scoring like cover matcher)
    t_low = t.lower()
    a_low = a.lower()

    def score(doc: dict) -> int:
        s = 0
        d_title = (doc.get("title") or "").strip().lower()
        if d_title == t_low:
            s += 50
        elif t_low in d_title:
            s += 25

        authors = doc.get("author_name")
        first_author = ""
        if isinstance(authors, list) and authors:
            first_author = (authors[0] or "").strip().lower()

        if a_low and first_author:
            if first_author == a_low:
                s += 50
            elif a_low in first_author or first_author in a_low:
                s += 25

        if doc.get("key"):
            s += 5
        return s

    best = max(docs, key=score)

    # Prefer work_key if present, else try from key (works can appear differently)
    work_keys = best.get("work_key")
    work_key = ""
    if isinstance(work_keys, list) and work_keys:
        work_key = str(work_keys[0] or "").strip()
    if not work_key:
        # sometimes docs have a "key" like "/works/OL..."
        k = str(best.get("key") or "").strip()
        if k.startswith("/works/"):
            work_key = k

    if not work_key:
        return ""

    work_url = f"https://openlibrary.org{work_key}.json"
    req2 = Request(work_url, headers={"User-Agent": "BookBuddy/1.0 (personal project)"})
    try:
        with urlopen(req2, timeout=timeout) as resp:
            work = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None  # network problem: try again next time

    desc = work.get("description")
    if isinstance(desc, dict):
        desc = desc.get("value")
    if isinstance(desc, str):
        # keep it tidy for UI
        desc = desc.strip()
        if len(desc) > 600:
            desc = desc[:600].rsplit(" ", 1)[0].rstrip() + "…"
        return desc

    return ""

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
    meta = _ol_best_match(title, author) or {}
    if cover_id:
        meta["cover_i"] = cover_id
        meta["cover"] = _ol_cover_url(cover_i=cover_id)
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
    top_rated = [_book_dict(bk, float(avg), count) for bk, avg, count in rows]
    first_book = db.session.scalar(db.select(Book).order_by(Book.title).limit(1))
    featured = top_rated[0] if top_rated else (_book_dict(first_book, 0.0) if first_book else {})
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


def _display_name(user) -> str:
    """How a reviewer is shown: their handle, else their name, else "reader"."""
    if not user:
        return "reader"
    return (user.handle or user.name or "").strip() or "reader"


def _book_dict(bk: Book, avg_rating: float, review_count: int = 0) -> dict:
    """The shape the templates expect for one book."""
    built = _ol_cover_url(cover_i=str(bk.cover_i or ""), isbn=str(bk.isbn or ""), olid=str(bk.olid or ""))
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


def _average_rating(book_id: int) -> float:
    avg = db.session.query(func.avg(Review.rating)).filter(Review.book_id == book_id).scalar()
    return float(avg or 0.0)


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
    reviews_for_template = [
        {
            "user": _display_name(r.user),
            "rating": r.rating,
            "text": (r.text or "").strip(),
            "age": _time_ago(r.created),
        }
        for r in latest_reviews
    ]

    # Fetched from Open Library once, then kept in the database.
    # Shown only when Open Library has one (the old made-up fallback text is gone).
    if bk.description is None:
        fetched = _ol_description_for(bk.title, bk.author)
        if fetched is not None:          # None means Open Library couldn't be reached
            bk.description = fetched     # may be "" if it has no description
            db.session.commit()
    description = bk.description or ""

    return render_template(
        "book_details.html",
        book=_book_dict(bk, _average_rating(bk.id)),
        book_id=bk.id,
        description=description,
        reviews=reviews_for_template,
    )


@app.route("/books/<int:book_id>/reviews")
def book_reviews(book_id):
    bk = db.get_or_404(Book, book_id)
    rows = Review.query.filter_by(book_id=bk.id).order_by(Review.created.desc()).all()
    reviews = [
        {
            "user": _display_name(r.user),
            "rating": r.rating,
            "text": (r.text or "").strip(),
            "created": r.created,
        }
        for r in rows
    ]
    return render_template(
        "book_reviews.html",
        book=_book_dict(bk, _average_rating(bk.id)),
        reviews=reviews,
        avg_rating=_average_rating(bk.id),
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

        cover = _book_dict(bk, 0.0)["cover"]
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

        meta = _ol_best_match(title, author) or {}
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
def is_following(follower_id: int, followed_id: int) -> bool:
    return Follow.query.filter_by(follower_id=follower_id, followed_id=followed_id).first() is not None


def follow_counts(user_id: int) -> tuple[int, int]:
    """(followers, following) for one user."""
    followers = Follow.query.filter_by(followed_id=user_id).count()
    following = Follow.query.filter_by(follower_id=user_id).count()
    return followers, following


def toggle_follow(follower: User, target: User) -> bool:
    """Follow target if not already following, otherwise unfollow. Returns True if now following."""
    existing = Follow.query.filter_by(follower_id=follower.id, followed_id=target.id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return False
    db.session.add(Follow(follower_id=follower.id, followed_id=target.id))
    db.session.commit()
    return True


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

    url = "https://openlibrary.org/search.json?q={}&limit=10".format(quote(q))

    try:
        req = Request(url, headers={"User-Agent": "BookBuddy/1.0 (personal project)"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return jsonify([])

    results = []
    for d in (data.get("docs") or []):
        title = (d.get("title") or "").strip()
        author = ""
        if isinstance(d.get("author_name"), list) and d["author_name"]:
            author = (d["author_name"][0] or "").strip()

        year = ""
        if isinstance(d.get("first_publish_year"), int):
            year = str(d["first_publish_year"])

        cover_i = str(d.get("cover_i") or "").strip()

        isbn = ""
        isbns = d.get("isbn")
        if isinstance(isbns, list) and isbns:
            isbn = str(isbns[0] or "").strip()

        olid = ""
        edition_keys = d.get("edition_key")
        if isinstance(edition_keys, list) and edition_keys:
            olid = str(edition_keys[0] or "").strip()

        cover = _ol_cover_url(cover_i=cover_i, isbn=isbn, olid=olid)

        if title and author:
            results.append({
                "title": title,
                "author": author,
                "year": year,
                "cover": cover,
                "cover_i": cover_i,
                "isbn": isbn,
                "olid": olid,
            })

    return jsonify(results)


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


from werkzeug.utils import secure_filename
import os

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
            filename = secure_filename(file.filename)

            
            allowed_ext = {"png", "jpg", "jpeg", "webp"}
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext in allowed_ext:
                # app.static_folder is the project's static/ folder wherever the
                # app is started from (the old relative path depended on that).
                upload_folder = os.path.join(app.static_folder, "uploads", "avatars")
                os.makedirs(upload_folder, exist_ok=True)

                
                base = filename.rsplit(".", 1)[0]
                filename = f"user_{current_user.id}_{base}.{ext}"

                filepath = os.path.join(upload_folder, filename)
                file.save(filepath)

                # Store path relative to /static because you do url_for('static', filename=user.avatar)
                current_user.avatar = f"uploads/avatars/{filename}"

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
        books = [_book_dict(r.book, _average_rating(r.book_id)) for r in reviewed]
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
    return render_template(
        "profile.html", user=user, your_reviews=reviews,
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
        if not new or len(new) < 6:
            flash("New password must be at least 6 characters.", "error")
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
def privacy_settings():
    if request.method == "POST":
        profile_private = bool(request.form.get("profile_private"))
        show_activity = bool(request.form.get("show_activity"))
        set_pref("profile_private", profile_private)
        set_pref("show_activity", show_activity)
        flash("Privacy settings saved.", "ok")
        return redirect(url_for("privacy_settings"))
    return render_template(
        "privacy_settings.html",
        profile_private=get_pref("profile_private", False),
        show_activity=get_pref("show_activity", True),
    )


@app.route("/notifications", methods=["GET", "POST"])
def notifications_settings():
    if request.method == "POST":
        enabled = bool(request.form.get("enabled"))
        set_pref("notifications_enabled", enabled)
        flash("Notifications preference updated.", "ok")
        return redirect(url_for("notifications_settings"))
    return render_template(
        "notification_settings.html",
        enabled=get_pref("notifications_enabled", True),
    )


@app.route("/theme", methods=["GET", "POST"])
def theme_settings():
    if request.method == "POST":
        theme = (request.form.get("theme") or "light").lower()
        if theme not in ("light", "dark"):
            theme = "light"
        set_pref("theme", theme)
        flash("Theme updated.", "ok")
        return redirect(url_for("theme_settings"))
    return render_template(
        "theme_settings.html",
        theme=get_pref("theme", "light"),
    )


@app.route("/language", methods=["GET", "POST"])
def language_settings():
    if request.method == "POST":
        lang = request.form.get("lang") or "English"
        if lang not in LANGS:
            lang = "English"
        set_pref("lang", lang)
        flash("Language set to {}".format(lang), "ok")
        return redirect(url_for("language_settings"))
    return render_template(
        "language_settings.html",
        langs=LANGS,
        current=get_pref("lang", "English"),
    )


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
    return jsonify([_book_dict(bk, float(avg), count) for bk, avg, count in rows])


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
        login_user(user, remember=True)
        return redirect(url_for("home"))

    return render_template("sign_up.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "")
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))
        login_user(user, remember=True)
        return redirect(url_for("home"))
    return render_template("sign_in.html")


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()

        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_reset_token(user.email)
            reset_link = url_for("reset_password", token=token, _external=True)

            
            print("\n=== PASSWORD RESET LINK ===")
            print(reset_link)
            print("===========================\n")

        flash("If that email exists, you’ll receive a password reset link shortly.", "ok")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    email = verify_reset_token(token, max_age_seconds=60 * 60)
    if not email:
        flash("That reset link is invalid or has expired. Please try again.", "error")
        return redirect(url_for("forgot_password"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("That reset link is invalid or has expired. Please try again.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_pw = (request.form.get("new_password") or "").strip()
        confirm = (request.form.get("confirm_password") or "").strip()

        if not new_pw or len(new_pw) < 6:
            flash("Password must be at least 6 characters.", "error")
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
