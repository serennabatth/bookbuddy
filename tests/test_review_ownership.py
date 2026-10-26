"""Tests that you can edit and delete your own reviews, and nobody else's."""
import pytest

from app.extensions import db
from app.models import Book, Review, User
from app.services import openlibrary


@pytest.fixture(autouse=True)
def no_open_library(monkeypatch):
    monkeypatch.setattr(openlibrary, "description_for", lambda *args, **kwargs: "")


def make_user(email):
    user = User(email=email)
    user.set_password("long-password")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def setup(app):
    """Two readers, one book, and a review written by the author."""
    author = make_user("author@example.com")
    make_user("stranger@example.com")
    book = Book(title="Dracula", author="Bram Stoker")
    db.session.add(book)
    db.session.commit()
    review = Review(user_id=author.id, book_id=book.id, book_title=book.title, rating=3, text="Fine.")
    db.session.add(review)
    db.session.commit()
    return review.id, book.id


def log_in(client, email):
    client.post("/login", data={"email": email, "password": "long-password"})


def test_author_can_edit_their_review(client, setup):
    review_id, _ = setup
    log_in(client, "author@example.com")
    client.post(f"/reviews/{review_id}/edit", data={"rating": "5", "review": "Changed my mind."})
    review = db.session.get(Review, review_id)
    assert (review.rating, review.text) == (5, "Changed my mind.")


def test_someone_else_cannot_edit_it(client, setup):
    review_id, _ = setup
    log_in(client, "stranger@example.com")
    response = client.post(f"/reviews/{review_id}/edit", data={"rating": "1", "review": "Vandalised"})
    assert response.status_code == 403
    assert db.session.get(Review, review_id).text == "Fine."


def test_someone_else_cannot_delete_it(client, setup):
    review_id, _ = setup
    log_in(client, "stranger@example.com")
    assert client.post(f"/reviews/{review_id}/delete").status_code == 403
    assert db.session.get(Review, review_id) is not None


def test_author_can_delete_their_review(client, setup):
    review_id, _ = setup
    log_in(client, "author@example.com")
    client.post(f"/reviews/{review_id}/delete")
    assert db.session.get(Review, review_id) is None


def test_edit_rejects_a_bad_rating(client, setup):
    review_id, _ = setup
    log_in(client, "author@example.com")
    client.post(f"/reviews/{review_id}/edit", data={"rating": "9", "review": "Too high"})
    assert db.session.get(Review, review_id).rating == 3


def test_edit_and_delete_only_show_on_your_own_review(client, setup):
    review_id, book_id = setup
    edit_link = f"/reviews/{review_id}/edit"

    log_in(client, "stranger@example.com")
    assert edit_link not in client.get(f"/books/{book_id}").get_data(as_text=True)
    client.post("/logout")

    log_in(client, "author@example.com")
    assert edit_link in client.get(f"/books/{book_id}").get_data(as_text=True)
