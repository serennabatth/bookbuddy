"""Tests for choosing covers and showing them."""
import pytest

from app import cli
from app.extensions import db
from app.models import Book
from app.services import openlibrary
from app.services.catalogue import PLACEHOLDER_COVER
from app.services.openlibrary import pick_best_search_result


def test_prefers_the_english_well_known_edition():
    french = {"title": "A Brief History of Time", "author_name": ["Stephen Hawking"],
              "cover_i": 1, "language": ["fre"], "edition_count": 3}
    english = {"title": "A Brief History of Time", "author_name": ["Stephen Hawking"],
               "cover_i": 2, "language": ["eng"], "edition_count": 90}
    assert pick_best_search_result([french, english], "A Brief History of Time", "Stephen Hawking") is english


def test_a_title_and_author_match_beats_popularity():
    popular_wrong = {"title": "Dune Messiah", "author_name": ["Frank Herbert"], "language": ["eng"], "edition_count": 100}
    exact = {"title": "Dune", "author_name": ["Frank Herbert"], "language": ["eng"], "edition_count": 10}
    assert pick_best_search_result([popular_wrong, exact], "Dune", "Frank Herbert") is exact


def test_no_results_gives_none():
    assert pick_best_search_result([], "Anything") is None


@pytest.fixture
def seed_file(tmp_path, monkeypatch):
    def write(text):
        path = tmp_path / "books.csv"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(cli, "SEED_FILE", str(path))
    return write


def test_a_hand_picked_cover_id_wins(app, seed_file, monkeypatch):
    monkeypatch.setattr(openlibrary, "best_match", lambda *a, **k: {"cover_i": "111", "cover": openlibrary.cover_url(cover_i="111")})
    seed_file("title,author,genre,cover_id\nDune,Frank Herbert,Sci-Fi,999\n")
    app.test_cli_runner().invoke(args=["seed"])
    assert Book.query.one().cover_i == "999"


def test_refresh_covers_updates_saved_books(app, seed_file, monkeypatch):
    db.session.add(Book(title="Dune", author="Frank Herbert", genre="Sci-Fi", cover_i=""))
    db.session.commit()
    monkeypatch.setattr(openlibrary, "best_match", lambda *a, **k: {"cover_i": "222", "cover": openlibrary.cover_url(cover_i="222")})
    seed_file("title,author,genre,cover_id\nDune,Frank Herbert,Sci-Fi,\n")
    app.test_cli_runner().invoke(args=["seed", "--refresh-covers"])
    assert Book.query.one().cover_i == "222"


def test_book_without_art_shows_its_title_tile(client, app):
    db.session.add(Book(title="Untitled Draft", author="Nobody", genre="Classics", cover=PLACEHOLDER_COVER))
    db.session.commit()
    html = client.get("/browse").get_data(as_text=True)
    assert 'class="book-cover-tile"' in html
    assert "placehold.co" not in html   # the grey "No Cover" image is gone


def test_book_with_art_layers_the_image_over_the_tile(client, app):
    db.session.add(Book(title="Dune", author="Frank Herbert", genre="Sci-Fi", cover_i="123"))
    db.session.commit()
    html = client.get("/browse").get_data(as_text=True)
    assert "covers.openlibrary.org/b/id/123-L.jpg?default=false" in html
    assert 'class="book-cover-tile"' in html
