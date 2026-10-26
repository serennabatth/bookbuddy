"""Tests for the book catalogue used by Home, Browse and Top Rated."""
import pytest

import main
from main import Book, Review, User, all_books_ui, db


@pytest.fixture
def no_internet(monkeypatch):
    """Make any Open Library call fail loudly, to prove none happen."""
    def fail(*args, **kwargs):
        raise AssertionError("all_books_ui() must not call Open Library")
    monkeypatch.setattr(main, "_ol_best_match", fail)


def test_catalogue_makes_no_network_calls(app, no_internet):
    books = all_books_ui()
    assert len(books) == len(main.BOOKS)  # the demo books, with an empty database


def test_catalogue_averages_ratings_per_book(app):
    reader = User(email="r@example.com")
    reader.set_password("long-password")
    db.session.add_all([
        reader,
        Book(title="Dracula", author="Bram Stoker"),
        Book(title="Emma", author="Jane Austen"),
    ])
    db.session.commit()
    db.session.add_all([
        Review(user_id=reader.id, book_title="Dracula", rating=4, text="Good"),
        Review(user_id=reader.id, book_title="Dracula", rating=2, text="Okay"),
    ])
    db.session.commit()

    ratings = {b["title"]: b["rating"] for b in all_books_ui()}

    assert ratings["Dracula"] == 3.0   # (4 + 2) / 2
    assert ratings["Emma"] == 0.0      # no reviews yet
