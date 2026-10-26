"""Tests for the catalogue: browsing, searching, Top Rated, pagination and seeding."""
import pytest

import main
from app.extensions import db
from app.models import Book, Favourite, Review, User
from app.services import openlibrary


@pytest.fixture(autouse=True)
def no_open_library(monkeypatch):
    """Never call the real Open Library from tests."""
    monkeypatch.setattr(openlibrary, "best_match", lambda *args, **kwargs: {})
    monkeypatch.setattr(openlibrary, "description_for", lambda *args, **kwargs: "")


def add_books(*pairs, genre="Classics"):
    books = [Book(title=t, author=a, genre=genre) for t, a in pairs]
    db.session.add_all(books)
    db.session.commit()
    return books


def add_reader(email):
    user = User(email=email)
    user.set_password("long-password")
    db.session.add(user)
    db.session.commit()
    return user


def review(user, book, rating):
    db.session.add(Review(user_id=user.id, book_id=book.id, book_title=book.title, rating=rating, text="ok"))
    db.session.commit()


def page_text(client, url):
    return client.get(url).get_data(as_text=True)


def test_search_matches_title_or_author_in_any_case(client, app):
    add_books(("Dracula", "Bram Stoker"), ("Emma", "Jane Austen"))
    assert "Dracula" in page_text(client, "/browse?q=dRaC")
    html = page_text(client, "/browse?q=austen")
    assert "Emma" in html and "Dracula" not in html


def test_search_treats_percent_signs_literally(client, app):
    add_books(("Dracula", "Bram Stoker"))
    assert "Dracula" not in page_text(client, "/browse?q=%25")  # %25 is a typed "%"


def test_genre_filter(client, app):
    add_books(("Dracula", "Bram Stoker"), genre="Gothic")
    add_books(("Dune", "Frank Herbert"), genre="Sci-Fi")
    html = page_text(client, "/browse?genre=Sci-Fi")
    assert "Dune" in html and "Dracula" not in html


def test_browse_is_paginated(client, app):
    add_books(*[(f"Book {i:02d}", "Author") for i in range(30)])
    first = page_text(client, "/browse?sort=title")
    second = page_text(client, "/browse?sort=title&page=2")
    assert "Book 00" in first and "Book 24" not in first      # 24 per page
    assert "Book 24" in second and "Book 29" in second
    assert "Page 2 of 2" in second


def test_top_rated_only_lists_reviewed_books_best_first(client, app):
    best, middling, _ = add_books(("Highly Rated Book", "A"), ("Middling Book", "B"), ("Never Reviewed Book", "C"))
    reader = add_reader("r@example.com")
    review(reader, best, 5)
    review(reader, middling, 3)

    html = page_text(client, "/top-rated")
    assert "Never Reviewed Book" not in html
    assert html.index("Highly Rated Book") < html.index("Middling Book")


def test_unreviewed_books_say_so_instead_of_zero(client, app):
    add_books(("Dracula", "Bram Stoker"))
    html = page_text(client, "/browse")
    assert "No reviews yet" in html
    assert "0.00 avg rating" not in html


def test_search_api_returns_at_most_eight_matches_with_ids(client, app):
    add_books(*[(f"Gothic tale {i}", "Author") for i in range(12)])
    results = client.get("/api/search?q=gothic").get_json()
    assert len(results) == 8
    assert all(isinstance(r["id"], int) for r in results)


def test_description_is_fetched_once_then_stored(client, app, monkeypatch):
    calls = []
    monkeypatch.setattr(openlibrary, "description_for",
                        lambda *args, **kwargs: calls.append(1) or "A vampire story.")
    (book,) = add_books(("Dracula", "Bram Stoker"))

    client.get(f"/books/{book.id}")
    client.get(f"/books/{book.id}")

    assert len(calls) == 1
    assert db.session.get(Book, book.id).description == "A vampire story."


def test_seed_adds_curated_books_once_and_fixes_genres(app, tmp_path, monkeypatch):
    csv_file = tmp_path / "books.csv"
    csv_file.write_text("title,author,genre\nDune,Frank Herbert,Sci-Fi\nEmma,Jane Austen,Romance\n", encoding="utf-8")
    monkeypatch.setattr(main, "SEED_FILE", str(csv_file))
    add_books(("Emma", "Jane Austen"), genre="Horror")   # already there, wrong genre
    runner = app.test_cli_runner()

    runner.invoke(args=["seed"])
    runner.invoke(args=["seed"])                          # running twice adds nothing new

    assert Book.query.count() == 2
    assert Book.query.filter_by(title="Emma").one().genre == "Romance"


def test_seed_reset_keeps_books_people_have_used(app, tmp_path, monkeypatch):
    csv_file = tmp_path / "books.csv"
    csv_file.write_text("title,author,genre\nDune,Frank Herbert,Sci-Fi\n", encoding="utf-8")
    monkeypatch.setattr(main, "SEED_FILE", str(csv_file))
    junk, loved = add_books(("Colouring Book", "Nobody"), ("Old Favourite", "Somebody"))
    reader = add_reader("r@example.com")
    db.session.add(Favourite(user_id=reader.id, book_id=loved.id))
    db.session.commit()

    result = app.test_cli_runner().invoke(args=["seed", "--reset"], input="y\n")

    titles = {b.title for b in Book.query.all()}
    assert result.exit_code == 0
    assert "Colouring Book" not in titles       # unused, not curated: removed
    assert "Old Favourite" in titles            # someone favourited it: kept
    assert "Dune" in titles
