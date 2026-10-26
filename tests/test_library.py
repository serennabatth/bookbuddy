"""Tests for favourites and history."""
import pytest

from app.extensions import db
from app.models import Book, Favourite, History


@pytest.fixture
def book(client):
    """A logged-in reader and one book."""
    client.post("/signup", data={"email": "reader@example.com", "password": "long-password"})
    book = Book(title="Dracula", author="Bram Stoker")
    db.session.add(book)
    db.session.commit()
    return book


def test_favourite_then_unfavourite(client, book):
    first = client.post("/api/favourites/toggle", json={"book_id": book.id}).get_json()
    second = client.post("/api/favourites/toggle", json={"book_id": book.id}).get_json()
    assert first["favourited"] is True
    assert second["favourited"] is False
    assert Favourite.query.count() == 0


def test_favourites_page_lists_favourited_books(client, book):
    client.post("/api/favourites/toggle", json={"book_id": book.id})
    assert "Dracula" in client.get("/favourites").get_data(as_text=True)


@pytest.mark.parametrize("book_id", [9999, "abc", None])
def test_favouriting_a_missing_book_is_a_404_not_a_crash(client, book, book_id):
    response = client.post("/api/favourites/toggle", json={"book_id": book_id})
    assert response.status_code == 404
    assert Favourite.query.count() == 0


def test_viewing_a_book_twice_keeps_one_history_entry(client, book):
    client.post("/api/history/add", json={"book_id": book.id})
    client.post("/api/history/add", json={"book_id": book.id})
    assert History.query.count() == 1


def test_history_for_a_missing_book_is_a_404(client, book):
    assert client.post("/api/history/add", json={"book_id": 9999}).status_code == 404
