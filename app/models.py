"""Database models.

After changing a model, create and run a migration:
    flask db migrate -m "what changed"
    flask db upgrade
"""
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager


def utc_now() -> datetime:
    """Current UTC time without timezone info (the way SQLite stores it).

    Replaces datetime.utcnow(), which is deprecated from Python 3.12.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), default="")
    # e.g. "@alexmorgan" - always lowercase and unique. None until the user picks one
    handle = db.Column(db.String(120), nullable=True, default=None)
    bio = db.Column(db.Text, default="")
    # path inside /static, e.g. "uploads/avatars/user_4_photo.jpg" ("" = no avatar)
    avatar = db.Column(db.String(255), default="")
    # private profiles hide reviews and lists from other users
    is_private = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    # unique index rather than a constraint - SQLite can add this without rebuilding the table
    __table_args__ = (db.Index("uq_user_handle", "handle", unique=True),)

    def set_password(self, pw: str) -> None:
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    user = db.relationship("User", backref=db.backref("reviews", lazy=True))

    # Linked by id now. Matching on title meant books with the same name shared reviews
    book_id = db.Column(
        db.Integer, db.ForeignKey("book.id", name="fk_review_book_id_book"),
        nullable=False, index=True,
    )
    book = db.relationship("Book", backref=db.backref("reviews", lazy=True))

    # TODO: remove these - old copies of the book details, pages use review.book now
    book_title = db.Column(db.String(255), nullable=False)
    book_author = db.Column(db.String(255), default="")
    book_cover = db.Column(db.String(500), default="")

    rating = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, default="")
    created = db.Column(db.DateTime, default=utc_now)

    __table_args__ = (
        # one review per user per book
        db.UniqueConstraint("user_id", "book_id", name="uq_review_user_book"),
        # ratings must be 1-5 (checked in the db too, not just the form)
        db.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating_range"),
    )


class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False, index=True)
    author = db.Column(db.String(255), nullable=False, index=True)
    genre = db.Column(db.String(120), default="Other")
    year = db.Column(db.String(20), default="")
    cover = db.Column(db.String(500), default="")

    # Open Library ids
    olid = db.Column(db.String(50), default="", index=True)
    cover_i = db.Column(db.String(50), default="")
    isbn = db.Column(db.String(32), default="")

    # Fetched from Open Library the first time the book page is opened.
    # None = not fetched yet, "" = no description available
    description = db.Column(db.Text, nullable=True)

    __table_args__ = (db.UniqueConstraint("title", "author", name="uq_book_title_author"),)


class Favourite(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=utc_now)

    __table_args__ = (db.UniqueConstraint("user_id", "book_id", name="uq_favourite_user_book"),)


class Follow(db.Model):
    """follower_id follows followed_id."""
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    followed_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    follower = db.relationship("User", foreign_keys=[follower_id])
    followed = db.relationship("User", foreign_keys=[followed_id])

    __table_args__ = (
        # can only follow someone once
        db.UniqueConstraint("follower_id", "followed_id", name="uq_follow_pair"),
        # and can't follow yourself
        db.CheckConstraint("follower_id != followed_id", name="ck_follow_not_self"),
    )


class History(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False, index=True)

    viewed_at = db.Column(db.DateTime, default=utc_now, index=True)

    __table_args__ = (db.UniqueConstraint("user_id", "book_id", name="uq_history_user_book"),)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    """Used by Flask-Login to get the logged-in user from the session."""
    return db.session.get(User, int(user_id))
