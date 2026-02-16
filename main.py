from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, send_file, jsonify, session
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    current_user, logout_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO
import qrcode
import qrcode.image.pil  # ensure PIL backend available
import os
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import text, func
from urllib.parse import quote
from urllib.request import urlopen, Request
import json


# -----------------------------------------------------------------------------
# App & DB
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("BOOKBUDDY_SECRET", "dev-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(app.instance_path, "bookbuddy.db")
os.makedirs(app.instance_path, exist_ok=True)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
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
    handle = db.Column(db.String(120), default="")  # store like "@serennabatth"
    bio = db.Column(db.Text, default="")

    def set_password(self, pw: str) -> None:
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    user = db.relationship("User", backref=db.backref("reviews", lazy=True))

    book_title = db.Column(db.String(255), nullable=False)
    book_author = db.Column(db.String(255), default="")
    book_cover = db.Column(db.String(500), default="")

    rating = db.Column(db.Integer, default=0)
    text = db.Column(db.Text, default="")
    created = db.Column(db.DateTime, default=datetime.utcnow)


class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False, index=True)
    author = db.Column(db.String(255), nullable=False, index=True)
    genre = db.Column(db.String(120), default="Other")
    year = db.Column(db.String(20), default="")
    cover = db.Column(db.String(500), default="")

    # Open Library identifiers (these make covers much more accurate)
    olid = db.Column(db.String(50), default="", index=True)     # e.g. "OL12345M" (edition id)
    cover_i = db.Column(db.String(50), default="")              # e.g. "1234567"
    isbn = db.Column(db.String(32), default="")                 # e.g. "9780141182636"

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


with app.app_context():
    db.create_all()

    # add bio column if it doesn't exist yet (sqlite dev migration)
    cols_user = [row[1] for row in db.session.execute(text("PRAGMA table_info(user)")).fetchall()]
    if "bio" not in cols_user:
        db.session.execute(text("ALTER TABLE user ADD COLUMN bio TEXT DEFAULT ''"))
        db.session.commit()

    # add Open Library columns to book if they don't exist yet (sqlite dev migration)
    cols_book = [row[1] for row in db.session.execute(text("PRAGMA table_info(book)")).fetchall()]
    if "olid" not in cols_book:
        db.session.execute(text("ALTER TABLE book ADD COLUMN olid TEXT DEFAULT ''"))
    if "cover_i" not in cols_book:
        db.session.execute(text("ALTER TABLE book ADD COLUMN cover_i TEXT DEFAULT ''"))
    if "isbn" not in cols_book:
        db.session.execute(text("ALTER TABLE book ADD COLUMN isbn TEXT DEFAULT ''"))
    db.session.commit()


# -----------------------------------------------------------------------------
# Demo Data (keep small; DB will hold the “proper app” library)
# -----------------------------------------------------------------------------
BOOKS = [
    {"title": "The Great Gatsby", "author": "F. Scott Fitzgerald", "genre": "Classics", "rating": 4.40,
     "cover": "https://covers.openlibrary.org/b/id/7222246-L.jpg"},
    {"title": "Jane Eyre", "author": "Charlotte Brontë", "genre": "Romance", "rating": 4.30,
     "cover": "https://covers.openlibrary.org/b/id/8226098-L.jpg"},
    {"title": "1984", "author": "George Orwell", "genre": "Dystopian", "rating": 4.60,
     "cover": "https://covers.openlibrary.org/b/id/7222246-L.jpg"},
    {"title": "Frankenstein", "author": "Mary Shelley", "genre": "Horror", "rating": 4.20,
     "cover": "https://covers.openlibrary.org/b/id/8378631-L.jpg"},
    {"title": "Crime and Punishment", "author": "Fyodor Dostoevsky", "genre": "Classics", "rating": 4.70,
     "cover": "https://covers.openlibrary.org/b/id/8100933-L.jpg"},
    {"title": "The Bell Jar", "author": "Sylvia Plath", "genre": "Literary", "rating": 4.10,
     "cover": "https://covers.openlibrary.org/b/id/8231856-L.jpg"},
    {"title": "The Picture of Dorian Gray", "author": "Oscar Wilde", "genre": "Gothic", "rating": 4.30,
     "cover": "https://covers.openlibrary.org/b/id/8229216-L.jpg"},
    {"title": "Moby-Dick", "author": "Herman Melville", "genre": "Adventure", "rating": 3.90,
     "cover": "https://covers.openlibrary.org/b/id/7222276-L.jpg"},
    {"title": "The Handmaid's Tale", "author": "Margaret Atwood", "genre": "Dystopian", "rating": 4.50,
     "cover": "https://covers.openlibrary.org/b/id/8235116-L.jpg"},
]

GENRES = [
    "Romance", "Fantasy", "Horror", "Mystery", "Non-fiction",
    "Sci-Fi", "Classics", "Dystopian", "Gothic", "Literary", "Adventure"
]

# ----------------------------
# App/session preferences (very lightweight demo)
# ----------------------------
LANGS = ["English", "Español", "Français", "Deutsch", "Italiano"]


def get_pref(key, default=None):
    return session.get(key, default)


def set_pref(key, value):
    session[key] = value


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
_DEMO_COVER_CACHE: dict[tuple[str, str], dict] = {}  # (title, author) -> {cover, cover_i, isbn, olid}


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


def all_books_ui():
    """
    Returns list of dicts for templates/JS: demo BOOKS + DB Book rows.
    Also adds computed avg rating from Review rows (by title match).

    Cover accuracy improvements:
      - DB books: prefer stored cover_i/isbn/olid to build cover URLs
      - Demo books: one-time Open Library lookup & cache
    """
    demo = []
    for b in BOOKS:
        title = b.get("title", "")
        author = b.get("author", "")
        key = (title, author)
        cover = b.get("cover") or ""

        if key not in _DEMO_COVER_CACHE:
            meta = _ol_best_match(title, author) or {}
            _DEMO_COVER_CACHE[key] = meta if meta.get("cover") else {"cover": cover}

        best_cover = (_DEMO_COVER_CACHE.get(key) or {}).get("cover") or cover

        demo.append({
            "title": title,
            "author": author,
            "genre": b.get("genre", "Other"),
            "year": b.get("year", ""),
            "cover": best_cover or PLACEHOLDER_COVER,
            "rating": float(b.get("rating", 0.0) or 0.0),
        })

    db_books = Book.query.all()
    db_list = []
    for bk in db_books:
        avg = (
            db.session.query(func.avg(Review.rating))
            .filter(Review.book_title == bk.title)
            .scalar()
        )

        built = _ol_cover_url(
            cover_i=str(getattr(bk, "cover_i", "") or ""),
            isbn=str(getattr(bk, "isbn", "") or ""),
            olid=str(getattr(bk, "olid", "") or ""),
        )
        final_cover = built or (bk.cover or "") or PLACEHOLDER_COVER

        db_list.append({
            "title": bk.title,
            "author": bk.author,
            "genre": bk.genre or "Other",
            "year": bk.year or "",
            "cover": final_cover,
            "rating": float(avg or 0.0),
        })

    return demo + db_list

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


def _ol_description_for(title: str, author: str = "", timeout: int = 10) -> str:
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

    # pick the most relevant doc (basic scoring like your cover matcher)
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

def get_or_create_book_row(book_dict: dict) -> Book:
    """Ensure a Book row exists for this UI book dict, return the row."""
    title = (book_dict.get("title") or "").strip()
    author = (book_dict.get("author") or "").strip()
    if not title or not author:
        return None

    existing = Book.query.filter_by(title=title, author=author).first()
    if existing:
        return existing

    b = Book(
        title=title,
        author=author,
        genre=book_dict.get("genre") or "Other",
        year=book_dict.get("year") or "",
        cover=book_dict.get("cover") or PLACEHOLDER_COVER,
    )
    db.session.add(b)
    db.session.commit()
    return b


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
        # keep curated for portfolio stability (no “random OL results”)
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



with app.app_context():
    # Auto-seed so the app feels “real” without manual adding
    _ensure_seeded_curated(min_books=250)


# -----------------------------------------------------------------------------
# Routes - Core Screens
# -----------------------------------------------------------------------------
@app.route("/")
def home():
    # Use combined list so homepage benefits from seeded DB too
    books = all_books_ui()
    featured = books[0] if books else (BOOKS[0] if BOOKS else {})
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


@app.route("/book-details/<title>")
def book_details(title):
    # Find the book from demo+DB
    book = next((b for b in all_books_ui() 
                 if (b.get("title") or "").lower() == (title or "").lower()), None)

    if not book:
        flash("Book not found.", "error")
        return redirect(url_for("browse"))

    # REAL reviews from DB for this book title (latest first)
    real_reviews = (
        Review.query
        .filter(Review.book_title == book["title"])
        .order_by(Review.created.desc())
        .limit(25)
        .all()
    )

    bk_row = get_or_create_book_row(book)
    book_id = bk_row.id if bk_row else None

    # Compute REAL average rating
    avg = (
        db.session.query(func.avg(Review.rating))
        .filter(Review.book_title == book["title"])
        .scalar()
    )
    avg_rating = float(avg or 0.0)

    # Update the book dict rating so your template shows the real avg
    book = dict(book)  # copy so we don't mutate the global list
    book["rating"] = avg_rating

    # Convert Review rows into the dict shape your template expects
    reviews_for_template = []
    for r in real_reviews:
        # prefer handle if available, else name, else email prefix
        display_user = ""
        if r.user:
            display_user = (r.user.handle or r.user.name or (r.user.email.split("@")[0] if r.user.email else "")).strip()
        if not display_user:
            display_user = "reader"

        reviews_for_template.append({
            "user": display_user,
            "rating": int(r.rating or 0),
            "text": (r.text or "").strip(),
            "age": _time_ago(r.created),
        })

    # Best-effort real description (fallback to your current placeholder)
    description = _ol_description_for(book.get("title", ""), book.get("author", "")) or (
        "A gripping, imaginative novel that explores power, control, and identity. "
        "This edition features a modern cover while preserving the timeless themes."
    )

    # Optional recommendations (if you ever enable that section in template)
    top_rated = sorted(all_books_ui(), key=lambda b: b.get("rating", 0.0), reverse=True)[:8]

    return render_template(
        "book_details.html",
        book=book,
        book_id=book_id,
        description=description,
        reviews=reviews_for_template,
        top_rated=top_rated,
    )

@app.route("/book/<title>/reviews")
def book_reviews(title):
    # Find the book from demo+DB (same logic as book_details)
    book = next(
        (b for b in all_books_ui()
         if (b.get("title") or "").lower() == (title or "").lower()),
        None
    )

    if not book:
        flash("Book not found.", "error")
        return redirect(url_for("browse"))

    # All REAL reviews for this book (newest first)
    rows = (
        Review.query
        .filter(Review.book_title == book["title"])
        .order_by(Review.created.desc())
        .all()
    )

    # Avg + count
    avg = (
        db.session.query(func.avg(Review.rating))
        .filter(Review.book_title == book["title"])
        .scalar()
    )
    avg_rating = float(avg or 0.0)
    review_count = len(rows)

    # Template-friendly shape (same as your book_details)
    reviews = []
    for r in rows:
        display_user = ""
        if r.user:
            display_user = (
                r.user.handle or r.user.name or (r.user.email.split("@")[0] if r.user.email else "")
            ).strip()
        if not display_user:
            display_user = "reader"

        reviews.append({
            "user": display_user,
            "rating": int(r.rating or 0),
            "text": (r.text or "").strip(),
            "created": r.created,
        })

    return render_template(
        "book_reviews.html",
        book=book,
        reviews=reviews,
        avg_rating=avg_rating,
        review_count=review_count,
    )



@app.route("/add-review", methods=["GET", "POST"])
@login_required
def add_review():
    if request.method == "POST":
        book_title = (request.form.get("book_title") or "").strip()
        review_text = (request.form.get("review") or "").strip()
        rating = int(request.form.get("rating") or 0)

        bk = next((b for b in all_books_ui() if (b.get("title") or "").lower() == book_title.lower()), None)

        if not bk or not review_text:
            flash("Pick a book and write a review first.", "error")
            return redirect(url_for("add_review"))

        review = Review(
            user_id=current_user.id,
            book_title=bk["title"],
            book_author=bk.get("author", ""),
            book_cover=bk.get("cover", ""),
            rating=rating,
            text=review_text,
        )
        db.session.add(review)
        db.session.commit()

        flash("Thanks for your review!", "ok")
        return redirect(url_for("profile_page"))

    return render_template("add_review.html", books=all_books_ui())


@app.route("/add-book", methods=["GET", "POST"])
def add_book():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        author = (request.form.get("author") or "").strip()
        genre = (request.form.get("genre") or "").strip() or "Other"
        year = (request.form.get("year") or "").strip()
        cover = (request.form.get("cover") or "").strip()

        if not title or not author:
            flash("Title and author are required.", "error")
            return redirect(url_for("add_book"))

        existing = Book.query.filter_by(title=title, author=author).first()
        if existing:
            flash("That book already exists.", "error")
            return redirect(url_for("add_review"))

        meta = _ol_best_match(title, author) or {}
        best_cover = meta.get("cover") or cover or PLACEHOLDER_COVER

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
            "title": b.title,
            "author": b.author,
            "cover": b.cover or PLACEHOLDER_COVER,
            "rating": 0.0,
        })

    return render_template("favourites.html", books=books, q=q)


@app.post("/api/favourites/toggle")
@login_required
def api_favourites_toggle():
    data = request.get_json(force=True) or {}
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
    data = request.get_json(force=True) or {}
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
    data = request.get_json(force=True) or {}
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
    data = request.get_json(force=True) or {}
    handle = (data.get("handle") or "").strip()
    if not handle:
        return {"ok": False, "error": "missing handle"}, 400

    idx = next((i for i, p in enumerate(FOLLOWERS) if p["handle"] == handle), None)
    if idx is None:
        return {"ok": False, "error": "not found"}, 404

    FOLLOWERS.pop(idx)
    return {"ok": True}


# ---------- Genre landing (pretty URL that reuses Browse) ----------
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
        "bio": current_user.bio or ""
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
        # Text fields
        new_name = (request.form.get("name") or "").strip()
        new_handle = (request.form.get("handle") or "").strip()
        new_bio = (request.form.get("bio") or "").strip()

        if new_name:
            current_user.name = new_name

        if new_handle:
            current_user.handle = (
                new_handle if new_handle.startswith("@") else f"@{new_handle}"
            )

        current_user.bio = new_bio[:200]

        # ----------------------------
        # Profile picture upload
        # ----------------------------
        file = request.files.get("profile_picture")

        if file and file.filename:
            filename = secure_filename(file.filename)

            upload_folder = os.path.join("static", "uploads", "avatars")
            os.makedirs(upload_folder, exist_ok=True)

            filepath = os.path.join(upload_folder, filename)
            file.save(filepath)

            # store path or filename on user model
            current_user.avatar = f"uploads/avatars/{filename}"

        db.session.commit()
        flash("Profile updated.", "ok")
        return redirect(url_for("profile_page"))

    user = {
        "name": current_user_name(),
        "handle": current_user_handle(),
        "bio": current_user.bio or "",
        "avatar": getattr(current_user, "avatar", None),
    }

    return render_template("edit_profile.html", user=user)


@app.route("/share-profile")
def share_profile():
    user = {"name": current_user_name(), "handle": current_user_handle()}
    return render_template("share_profile.html", user=user, books=BOOKS)


@app.route("/qr-profile")
def qr_profile():
    url = request.args.get("url")
    if not url:
        handle = current_user_handle().replace("@", "")
        url = url_for("public_profile", handle=handle, _external=True)

    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/public/<handle>")
def public_profile(handle):
    shown_handle = f"@{handle}" if not handle.startswith("@") else handle
    user = {"name": current_user_name(), "handle": shown_handle}
    return render_template("profile.html", user=user, your_reviews=BOOKS)


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
        handle = handle_raw if handle_raw.startswith("@") else f"@{handle_raw}" if handle_raw else ""

        if not email or not password:
            flash("Email and password are required.", "error")
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


@app.route("/logout")
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

            # DEV: print link in terminal for now
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
    print(">>> http://127.0.0.1:5000")
    app.run(debug=True)
