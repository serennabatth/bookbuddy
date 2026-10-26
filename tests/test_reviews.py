"""Tests for book pages and writing reviews."""
import pytest
from sqlalchemy.exc import IntegrityError

import main
from main import Book, Review, User, db


@pytest.fixture(autouse=True)
def no_open_library(monkeypatch):
    """Book pages fetch a description from Open Library; use a fake one in tests."""
    monkeypatch.setattr(main, "_ol_description_for", lambda *args, **kwargs: "")


@pytest.fixture
def book(app):
    bk = Book(title="Dracula", author="Bram Stoker")
    db.session.add(bk)
    db.session.commit()
    return bk


def log_in(client, email="reader@example.com"):
    client.post("/signup", data={"email": email, "password": "long-password"})
    return User.query.filter_by(email=email).first()


def post_review(client, book_id, rating="4", text="Gripping."):
    return client.post("/add-review", data={"book_id": book_id, "rating": rating, "review": text})


def test_review_is_linked_to_the_right_book(client, book):
    user = log_in(client)
    post_review(client, book.id)
    review = Review.query.one()
    assert review.book_id == book.id
    assert review.user_id == user.id


@pytest.mark.parametrize("bad_rating", ["0", "6", "-1", "abc", ""])
def test_review_rejects_bad_ratings(client, book, bad_rating):
    log_in(client)
    response = post_review(client, book.id, rating=bad_rating)
    assert response.status_code == 302       # sent back with a message, not a crash
    assert Review.query.count() == 0


def test_one_review_per_reader_per_book(client, book):
    log_in(client)
    post_review(client, book.id, text="First")
    post_review(client, book.id, text="Second")
    assert Review.query.count() == 1


def test_database_itself_rejects_out_of_range_ratings(app, book):
    # Even if a bug skipped the route's checks, the CHECK constraint stops it.
    user = User(email="x@example.com")
    user.set_password("long-password")
    db.session.add(user)
    db.session.commit()
    db.session.add(Review(user_id=user.id, book_id=book.id, book_title=book.title, rating=9))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_database_rejects_a_review_for_a_missing_book(app):
    # Foreign keys are switched on for SQLite, so book 999 must exist.
    user = User(email="y@example.com")
    user.set_password("long-password")
    db.session.add(user)
    db.session.commit()
    db.session.add(Review(user_id=user.id, book_id=999, book_title="Ghost", rating=3))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_book_page_works_for_titles_with_a_slash(client, app):
    bk = Book(title="Either/Or", author="Søren Kierkegaard")
    db.session.add(bk)
    db.session.commit()
    response = client.get(f"/books/{bk.id}")
    assert response.status_code == 200
    assert "Either/Or" in response.get_data(as_text=True)


def test_old_title_links_redirect_to_the_new_address(client, book):
    response = client.get("/book-details/Dracula")
    assert response.status_code == 301
    assert response.headers["Location"].endswith(f"/books/{book.id}")


def test_unknown_book_is_a_404(client, app):
    assert client.get("/books/12345").status_code == 404
