"""Tests for the Your Library page, the shelf pages and reading stats."""
from datetime import datetime

import pytest

from app.extensions import db
from app.models import Book, Favourite, Review, ShelfEntry, User
from app.services.shelves import set_shelf
from app.services.stats import reading_stats


@pytest.fixture
def reader(client):
    client.post("/signup", data={"email": "reader@example.com", "password": "long-password"})
    return User.query.filter_by(email="reader@example.com").one()


def add_book(title, genre="Classics"):
    book = Book(title=title, author="Someone", genre=genre)
    db.session.add(book)
    db.session.commit()
    return book


def page(client, url="/library"):
    return " ".join(client.get(url).get_data(as_text=True).split())


def test_needs_login(client):
    assert "/login" in client.get("/library").headers["Location"]


def test_new_reader_sees_the_empty_state(client, reader):
    html = page(client)
    assert "Start your library" in html
    assert "Your reading in" not in html


def test_books_appear_on_their_shelves(client, reader):
    set_shelf(reader.id, add_book("Middlemarch").id, "reading")
    set_shelf(reader.id, add_book("Dune").id, "want")
    html = page(client)
    assert "Middlemarch" in html and "Finished it" in html
    assert "Dune" in html
    assert "2 books across your shelves" in html


def test_finished_it_moves_the_book_and_returns_to_the_library(client, reader):
    book = add_book("Middlemarch")
    set_shelf(reader.id, book.id, "reading")
    response = client.post(f"/books/{book.id}/shelf", data={"status": "finished", "next": "/library"})
    assert response.headers["Location"].endswith("/library")
    assert ShelfEntry.query.one().status == "finished"


def test_favourites_get_a_heart(client, reader):
    book = add_book("Rebecca")
    db.session.add(Favourite(user_id=reader.id, book_id=book.id))
    db.session.commit()
    assert 'class="lib-heart"' in page(client)


def test_see_all_shelf_page(client, reader):
    set_shelf(reader.id, add_book("Dune").id, "want")
    assert "Dune" in page(client, "/library/want")
    assert client.get("/library/abandoned").status_code == 404


def test_library_tab_is_in_the_menu(client, reader):
    assert 'aria-label="Library"' in page(client, "/")


def test_reading_stats_only_count_this_year(app, reader):
    gothic1, gothic2, sci_fi = add_book("A", "Gothic"), add_book("B", "Gothic"), add_book("C", "Sci-Fi")
    for book in (gothic1, gothic2, sci_fi):
        set_shelf(reader.id, book.id, "finished")
    # fixed dates so the test doesn't depend on today's date - one was finished last year
    for book, finished in [(gothic1, datetime(2026, 3, 1)), (gothic2, datetime(2026, 5, 1)), (sci_fi, datetime(2025, 6, 1))]:
        ShelfEntry.query.filter_by(book_id=book.id).one().finished_at = finished
    db.session.add_all([
        Review(user_id=reader.id, book_id=gothic1.id, book_title="A", rating=5, text="x", created=datetime(2026, 3, 1)),
        Review(user_id=reader.id, book_id=gothic2.id, book_title="B", rating=4, text="x", created=datetime(2026, 4, 1)),
        Review(user_id=reader.id, book_id=sci_fi.id, book_title="C", rating=1, text="x", created=datetime(2025, 4, 1)),
    ])
    db.session.commit()

    stats = reading_stats(reader.id, 2026)
    assert stats == {"year": 2026, "finished": 2, "reviews": 2, "avg_rating": 4.5, "top_genre": "Gothic"}


def test_stats_with_nothing_yet(app, reader):
    assert reading_stats(reader.id, 2026)["avg_rating"] is None
