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
from sqlalchemy import event, func
from sqlalchemy.engine import Engine
from urllib.parse import quote, urlparse
from urllib.request import urlopen, Request
import json
from functools import lru_cache


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

    __table_args__ = (db.UniqueConstraint("handle", name="uq_user_handle"),)

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

    __table_args__ = (db.UniqueConstraint("title", "author", name="uq_book_title_author"),)

class Favourite(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "book_id", name="uq_favourite_user_book"),)


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
    """
    Build the most reliable Open Library cover URL available.
    Priority:
      1) cover_i (best)
      2) ISBN (often good)
      3) OLID (edition id)
    """
    cover_i = (cover_i or "").strip()
    isbn = (isbn or "").strip()
    olid = (olid or "").strip()

    if cover_i:
        return f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"
    if isbn:
        return f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
    if olid:
        return f"https://covers.openlibrary.org/b/olid/{olid}-L.jpg"
    return ""


def _ol_best_match(title: str, author: str = "", timeout: int = 15) -> dict:
    """
    Try to find the best Open Library match for (title, author).
    Returns: {cover, cover_i, isbn, olid, year}
    """
    t = (title or "").strip()
    a = (author or "").strip()
    if not t:
        return {}

    # Query includes author to reduce wrong covers
    q = t if not a else f"{t} {a}"
    url = f"https://openlibrary.org/search.json?q={quote(q)}&limit=20&page=1"
    req = Request(url, headers={"User-Agent": "BookBuddy/1.0 (personal project)"})

    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}

    docs = data.get("docs") or []
    if not docs:
        return {}

    t_low = t.lower()
    a_low = a.lower()

    def score(doc: dict) -> int:
        s = 0
        d_title = (doc.get("title") or "").strip().lower()
        if d_title == t_low:
            s += 50
        elif t_low and t_low in d_title:
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

        if doc.get("cover_i"):
            s += 10

        isbns = doc.get("isbn")
        if isinstance(isbns, list) and isbns:
            s += 3

        return s

    best = max(docs, key=score)

    cover_i = str(best.get("cover_i") or "").strip()

    olid = ""
    edition_keys = best.get("edition_key")
    if isinstance(edition_keys, list) and edition_keys:
        olid = str(edition_keys[0] or "").strip()

    isbn = ""
    isbns = best.get("isbn")
    if isinstance(isbns, list) and isbns:
        isbn = str(isbns[0] or "").strip()

    year = ""
    if isinstance(best.get("first_publish_year"), int):
        year = str(best["first_publish_year"])

    cover = _ol_cover_url(cover_i=cover_i, isbn=isbn, olid=olid)

    return {"cover": cover, "cover_i": cover_i, "isbn": isbn, "olid": olid, "year": year}


def all_books_ui() -> list[dict]:
    """Every book as a dict for templates and JavaScript, with its average rating.

    All average ratings come from ONE grouped query, not one per book.
    Task 5 replaces this with paginated SQL queries.
    """
    # {book id: average rating} for every reviewed book.
    avg_by_book = dict(
        db.session.query(Review.book_id, func.avg(Review.rating))
        .group_by(Review.book_id)
        .all()
    )

    books = []
    for bk in Book.query.all():
        built = _ol_cover_url(
            cover_i=str(bk.cover_i or ""),
            isbn=str(bk.isbn or ""),
            olid=str(bk.olid or ""),
        )
        books.append({
            "id": bk.id,
            "title": bk.title,
            "author": bk.author,
            "genre": bk.genre or "Other",
            "year": bk.year or "",
            "cover": built or safe_cover_url(bk.cover) or PLACEHOLDER_COVER,
            "rating": float(avg_by_book.get(bk.id) or 0.0),
        })
    return books


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


@lru_cache(maxsize=1024)
def _ol_description_for(title: str, author: str = "", timeout: int = 4) -> str:
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
        return ""

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
        return ""

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
# Bulk seeding helper (Open Library) + curated search terms
# -----------------------------------------------------------------------------
def _ol_search(term: str, limit: int = 50, page: int = 1):
    """
    Search Open Library and return list of dicts:
      {title, author, year, cover, cover_i, isbn, olid}
    """
    url = f"https://openlibrary.org/search.json?q={quote(term)}&limit={limit}&page={page}"
    req = Request(url, headers={"User-Agent": "BookBuddy/1.0 (personal project)"})

    with urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

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

    return results


# ---------------------------------------------------------------------
# Curated shelves (seed by title/author, not broad search terms)
# ---------------------------------------------------------------------
CURATED_SHELVES = {
    "Classics": [
        ("Pride and Prejudice", "Jane Austen", "Classics"),
        ("Wuthering Heights", "Emily Brontë", "Classics"),
        ("Jane Eyre", "Charlotte Brontë", "Classics"),
        ("Great Expectations", "Charles Dickens", "Classics"),
        ("The Great Gatsby", "F. Scott Fitzgerald", "Classics"),
        ("Crime and Punishment", "Fyodor Dostoevsky", "Classics"),
        ("Frankenstein", "Mary Shelley", "Classics"),
        ("Dracula", "Bram Stoker", "Classics"),
        ("The Picture of Dorian Gray", "Oscar Wilde", "Classics"),
        ("1984", "George Orwell", "Classics"),
        ("Brave New World", "Aldous Huxley", "Classics"),
    ],
    "Cult favourites": [
        ("The Secret History", "Donna Tartt", "Literary"),
        ("Fight Club", "Chuck Palahniuk", "Literary"),
        ("American Psycho", "Bret Easton Ellis", "Literary"),
        ("The Handmaid's Tale", "Margaret Atwood", "Dystopian"),
        ("The Bell Jar", "Sylvia Plath", "Literary"),
        ("The Road", "Cormac McCarthy", "Literary"),
        ("Gone Girl", "Gillian Flynn", "Mystery"),
        ("The Shining", "Stephen King", "Horror"),
    ],
    "Trending": [
        
        ("Fourth Wing", "Rebecca Yarros", "Fantasy"),
        ("Iron Flame", "Rebecca Yarros", "Fantasy"),
        ("The Seven Husbands of Evelyn Hugo", "Taylor Jenkins Reid", "Romance"),
        ("The Song of Achilles", "Madeline Miller", "Fantasy"),
        ("It Ends with Us", "Colleen Hoover", "Romance"),
        ("The Silent Patient", "Alex Michaelides", "Mystery"),
    ],
}



def _ensure_seeded_curated(min_books: int = 250):
    ...
    """
    Seed DB using curated title/author lists (stable, portfolio-friendly).
    Keeps going until we have at least min_total books in DB.
    """
    current_count = Book.query.count()
    if current_count >= min_books:
        return

    for shelf, items in CURATED_SHELVES.items():
        for title, author, genre in items:
            title = (title or "").strip()
            author = (author or "").strip()
            genre = (genre or "Other").strip()

            if not title or not author:
                continue

            if Book.query.filter_by(title=title, author=author).first():
                continue

            meta = _ol_best_match(title, author) or {}
            cover = (meta.get("cover") or "").strip() or PLACEHOLDER_COVER

            b = Book(
                title=title,
                author=author,
                genre=genre,
                year=(meta.get("year") or "").strip(),
                cover=cover,
                olid=(meta.get("olid") or "").strip(),
                cover_i=(meta.get("cover_i") or "").strip(),
                isbn=(meta.get("isbn") or "").strip(),
            )
            db.session.add(b)

    db.session.commit()



@app.cli.command("seed")
def seed_command() -> None:
    """Add the curated starter books. Needs an internet connection.

    This used to run every time main.py was imported, which made start-up
    wait on Open Library and broke `flask db` commands on an empty database.
    Run it once with:  flask --app main seed
    """
    _ensure_seeded_curated(min_books=250)
    print(f"The catalogue now has {Book.query.count()} books.")


# -----------------------------------------------------------------------------
# Routes - Core Screens
# -----------------------------------------------------------------------------
@app.route("/")
def home():
    # Use combined list so homepage benefits from seeded DB too
    books = all_books_ui()
    featured = books[0] if books else {}
    top_rated = sorted(books, key=lambda b: b.get("rating", 0.0), reverse=True)[:6]
    return render_template(
        "index.html",
        featured=featured,
        genres=GENRES,
        top_rated=top_rated,
    )


@app.route("/browse")
def browse():
    genre = (request.args.get("genre") or "").strip()
    q = (request.args.get("q") or "").strip().lower()

    books = all_books_ui()

    if genre:
        books = [b for b in books if (b.get("genre") or "").lower() == genre.lower()]
    if q:
        books = [
            b for b in books
            if q in (b.get("title") or "").lower() or q in (b.get("author") or "").lower()
        ]

    return render_template(
        "browse.html",
        books=books,
        genres=GENRES,
        selected_genre=genre
    )


def _display_name(user) -> str:
    """How a reviewer is shown: their handle, else their name, else "reader"."""
    if not user:
        return "reader"
    return (user.handle or user.name or "").strip() or "reader"


def _book_dict(bk: Book, avg_rating: float) -> dict:
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

    # Shown only when Open Library has one. The old made-up fallback text is gone.
    description = _ol_description_for(bk.title, bk.author)

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

    return render_template("add_review.html", books=all_books_ui())


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

    books = []
    for b in rows:
        if q and (q not in (b.title or "").lower()) and (q not in (b.author or "").lower()):
            continue
        books.append({
            "id": b.id,
            "title": b.title,
            "author": b.author,
            "cover": b.cover or PLACEHOLDER_COVER,
            "rating": 0.0,
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
            "rating": 0.0,
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
    q = (request.args.get("q") or "").strip().lower()
    books = sorted(all_books_ui(), key=lambda b: b.get("rating", 0.0), reverse=True)
    if q:
        books = [b for b in books if q in (b.get("title","").lower()) or q in (b.get("author","").lower())]
    return render_template("top_rated.html", books=books)



# ----------------------------
# SOCIAL: Following / Followers (demo in-memory)
# ----------------------------
FOLLOWING = [
    {"name": "John Smith",   "handle": "@johnsmith34"},
    {"name": "Emma Davis",   "handle": "@emmadavis"},
    {"name": "Sophia Nguyen","handle": "@sophiareads"},
    {"name": "Oliver Brown", "handle": "@olibrown"},
    {"name": "Lucas Garcia", "handle": "@l_garcia55"},
    {"name": "Lily Gordon",  "handle": "@lilygordon01"},
]

FOLLOWERS = [
    {"name": "Ava Patel",     "handle": "@avap_"},
    {"name": "Ben Carter",    "handle": "@bencarter"},
    {"name": "Chen Wei",      "handle": "@chenreads"},
    {"name": "Diana Lopez",   "handle": "@dianalpz"},
    {"name": "Ethan Murphy",  "handle": "@ethanm"},
    {"name": "Fatima Khan",   "handle": "@fatimakhan"},
]


@app.route("/following")
def following():
    q = (request.args.get("q") or "").strip().lower()
    people = FOLLOWING[:]
    if q:
        people = [p for p in people if q in p["name"].lower() or q in p["handle"].lower()]
    return render_template("following.html", people=people)


@app.route("/followers")
def followers():
    q = (request.args.get("q") or "").strip().lower()
    people = FOLLOWERS[:]
    if q:
        people = [p for p in people if q in p["name"].lower() or q in p["handle"].lower()]
    return render_template("followers.html", people=people)


@app.route("/api/following/toggle", methods=["POST"])
def api_following_toggle():
    data = request.get_json(silent=True) or {}
    handle = (data.get("handle") or "").strip()
    if not handle:
        return {"ok": False, "error": "missing handle"}, 400

    idx = next((i for i, p in enumerate(FOLLOWING) if p["handle"] == handle), None)
    if idx is not None:
        FOLLOWING.pop(idx)
        return {"ok": True, "state": "unfollowed"}
    else:
        name_guess = handle.lstrip("@").replace("_", " ").title() or "User"
        FOLLOWING.append({"name": name_guess, "handle": handle})
        return {"ok": True, "state": "followed"}


@app.route("/api/followers/remove", methods=["POST"])
def api_followers_remove():
    data = request.get_json(silent=True) or {}
    handle = (data.get("handle") or "").strip()
    if not handle:
        return {"ok": False, "error": "missing handle"}, 400

    idx = next((i for i, p in enumerate(FOLLOWERS) if p["handle"] == handle), None)
    if idx is None:
        return {"ok": False, "error": "not found"}, 404

    FOLLOWERS.pop(idx)
    return {"ok": True}


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

    return render_template("profile.html", user=user, your_reviews=recent)


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
    return render_template("profile.html", user=user, your_reviews=reviews)


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
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify([])

    books = all_books_ui()

    def match(b):
        return q in (b.get("title") or "").lower() or q in (b.get("author") or "").lower()

    results = [
        {
            "id": b.get("id"),
            "title": b.get("title", ""),
            "author": b.get("author", ""),
            "cover": b.get("cover", ""),
            "rating": b.get("rating", 0),
            "genre": b.get("genre", "")
        }
        for b in books if match(b)
    ][:8]
    return jsonify(results)


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
