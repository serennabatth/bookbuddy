"""Tests for adding books safely."""
import pytest

import main
from main import Book, safe_cover_url


@pytest.fixture(autouse=True)
def no_open_library(monkeypatch):
    """Stop tests calling the real Open Library website.

    monkeypatch swaps the function for a fake one during each test, so the
    tests are fast, work offline and always get the same answer.
    """
    monkeypatch.setattr(main, "_ol_best_match", lambda *args, **kwargs: {})


def log_in(client):
    client.post("/signup", data={"email": "reader@example.com", "password": "long-password"})


@pytest.mark.parametrize("url, expected", [
    ("https://covers.openlibrary.org/b/id/1-L.jpg", "https://covers.openlibrary.org/b/id/1-L.jpg"),
    ("javascript:alert(1)", ""),
    ("http://example.com/cover.jpg", ""),       # not encrypted
    ('https://x.com/a.jpg" onerror="alert(1)', ""),  # tries to break out of the HTML attribute
    ("", ""),
])
def test_safe_cover_url(url, expected):
    assert safe_cover_url(url) == expected


def test_add_book_requires_login(client):
    response = client.post("/add-book", data={"title": "Dracula", "author": "Bram Stoker"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert Book.query.count() == 0


def test_add_book_drops_an_unsafe_cover(client):
    log_in(client)
    client.post("/add-book", data={
        "title": "Test Book", "author": "Test Author",
        "cover": 'x" onerror="alert(1)',
    })
    book = Book.query.filter_by(title="Test Book").first()
    assert book is not None
    assert "onerror" not in book.cover
    assert book.cover == main.PLACEHOLDER_COVER
