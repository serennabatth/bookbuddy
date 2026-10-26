"""Tests for the Currently reading strip on Home."""
import pytest

from app.extensions import db
from app.models import Book, ShelfEntry, User
from app.services.shelves import set_shelf


@pytest.fixture
def book(app):
    book = Book(title="Middlemarch", author="George Eliot")
    db.session.add(book)
    db.session.commit()
    return book


def sign_up(client):
    client.post("/signup", data={"email": "reader@example.com", "password": "long-password"})
    return User.query.filter_by(email="reader@example.com").one()


def home(client):
    return client.get("/").get_data(as_text=True)


def test_strip_shows_books_being_read(client, book):
    reader = sign_up(client)
    set_shelf(reader.id, book.id, "reading")
    html = home(client)
    assert 'class="home-reading"' in html
    assert "Middlemarch" in html and "Finished it" in html


def test_no_strip_when_nothing_is_being_read(client, book):
    reader = sign_up(client)
    set_shelf(reader.id, book.id, "want")
    assert 'class="home-reading"' not in home(client)


def test_no_strip_when_logged_out(client, book):
    assert 'class="home-reading"' not in home(client)


def test_finished_it_on_home_stays_on_home(client, book):
    reader = sign_up(client)
    set_shelf(reader.id, book.id, "reading")
    response = client.post(f"/books/{book.id}/shelf", data={"status": "finished", "next": "/"})
    assert response.headers["Location"].endswith("/")
    assert ShelfEntry.query.one().status == "finished"
    assert 'class="home-reading"' not in home(client)   # gone from the strip
