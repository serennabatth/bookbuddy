"""Tests for the heart shown on favourited books' covers."""
import pytest

from app.extensions import db
from app.models import Book, Favourite, Review, User

HEART = 'class="book-cover-heart"'


@pytest.fixture
def books(app):
    loved, other = Book(title="Rebecca", author="Daphne du Maurier"), Book(title="Dune", author="Frank Herbert")
    db.session.add_all([loved, other])
    db.session.commit()
    return loved, other


def sign_up_and_favourite(client, book):
    client.post("/signup", data={"email": "reader@example.com", "password": "long-password"})
    reader = User.query.filter_by(email="reader@example.com").one()
    db.session.add(Favourite(user_id=reader.id, book_id=book.id))
    db.session.commit()
    return reader


def html(client, url):
    return client.get(url).get_data(as_text=True)


def test_browse_shows_a_heart_only_on_favourites(client, books):
    loved, _ = books
    sign_up_and_favourite(client, loved)
    page = html(client, "/browse")
    assert page.count(HEART) == 1
    assert "Cover of Rebecca by Daphne du Maurier (in your favourites)" in page


def test_top_rated_shows_hearts_too(client, books):
    loved, _ = books
    reader = sign_up_and_favourite(client, loved)
    db.session.add(Review(user_id=reader.id, book_id=loved.id, book_title=loved.title, rating=5, text="Great"))
    db.session.commit()
    assert HEART in html(client, "/top-rated")


def test_no_hearts_when_logged_out(client, books):
    assert HEART not in html(client, "/browse")
