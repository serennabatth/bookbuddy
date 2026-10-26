"""Tests for sorting a book's reviews."""
from datetime import datetime

import pytest

from app.extensions import db
from app.models import Book, Review, User
from app.services import openlibrary


@pytest.fixture(autouse=True)
def no_descriptions(monkeypatch):
    monkeypatch.setattr(openlibrary, "description_for", lambda *args, **kwargs: "")


@pytest.fixture
def book(app):
    """A book with three reviews: oldest rated 3, middle rated 5, newest rated 1."""
    book = Book(title="Dracula", author="Bram Stoker")
    db.session.add(book)
    db.session.commit()
    for i, (rating, text, day) in enumerate([(3, "Review OK", 1), (5, "Review GREAT", 2), (1, "Review POOR", 3)]):
        user = User(email=f"r{i}@example.com")
        user.set_password("long-password")
        db.session.add(user)
        db.session.commit()
        db.session.add(Review(user_id=user.id, book_id=book.id, book_title=book.title,
                              rating=rating, text=text, created=datetime(2026, 1, day)))
    db.session.commit()
    return book


def order(client, url):
    html = client.get(url).get_data(as_text=True)
    return sorted(["OK", "GREAT", "POOR"], key=lambda word: html.index(f"Review {word}"))


@pytest.mark.parametrize("sort, expected", [
    ("newest", ["POOR", "GREAT", "OK"]),
    ("highest", ["GREAT", "OK", "POOR"]),
    ("lowest", ["POOR", "OK", "GREAT"]),
    ("nonsense", ["POOR", "GREAT", "OK"]),   # unknown values fall back to newest
])
def test_book_page_sorts_reviews(client, book, sort, expected):
    assert order(client, f"/books/{book.id}?sort={sort}") == expected


def test_all_reviews_page_sorts_too(client, book):
    assert order(client, f"/books/{book.id}/reviews?sort=highest") == ["GREAT", "OK", "POOR"]


def test_chosen_sort_is_selected_in_the_dropdown(client, book):
    html = client.get(f"/books/{book.id}?sort=lowest").get_data(as_text=True)
    assert '<option value="lowest" selected>' in html


def test_all_reviews_page_shows_how_long_ago(client, book):
    # this page used to leave the date blank
    assert "ago" in client.get(f"/books/{book.id}/reviews").get_data(as_text=True)
