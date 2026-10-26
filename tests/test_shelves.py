"""Tests for shelves (want to read / reading / finished)."""
import pytest
from sqlalchemy.exc import IntegrityError

from app import cli
from app.extensions import db
from app.models import Book, ShelfEntry, User
from app.services import openlibrary
from app.services.shelves import books_on_shelf, set_shelf, shelf_counts


@pytest.fixture(autouse=True)
def no_descriptions(monkeypatch):
    monkeypatch.setattr(openlibrary, "description_for", lambda *args, **kwargs: "")


@pytest.fixture
def reader(app):
    user = User(email="reader@example.com")
    user.set_password("long-password")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def book(app):
    book = Book(title="Dracula", author="Bram Stoker")
    db.session.add(book)
    db.session.commit()
    return book


def log_in(client):
    client.post("/login", data={"email": "reader@example.com", "password": "long-password"})


def shelve(client, book, status):
    return client.post(f"/books/{book.id}/shelf", data={"status": status})


def test_reading_sets_started_and_finishing_keeps_it(reader, book):
    set_shelf(reader.id, book.id, "reading")
    entry = set_shelf(reader.id, book.id, "finished")
    assert entry.status == "finished"
    assert entry.started_at is not None
    assert entry.finished_at is not None


def test_moving_back_to_want_clears_the_dates(reader, book):
    set_shelf(reader.id, book.id, "finished")
    entry = set_shelf(reader.id, book.id, "want")
    assert entry.started_at is None and entry.finished_at is None


def test_a_book_is_only_ever_on_one_shelf(reader, book):
    for status in ["want", "reading", "finished"]:
        set_shelf(reader.id, book.id, status)
    assert ShelfEntry.query.count() == 1


def test_counts_and_listing(reader, book):
    other = Book(title="Emma", author="Jane Austen")
    db.session.add(other)
    db.session.commit()
    set_shelf(reader.id, book.id, "reading")
    set_shelf(reader.id, other.id, "want")
    assert shelf_counts(reader.id) == {"want": 1, "reading": 1, "finished": 0}
    assert [b.title for b, _ in books_on_shelf(reader.id, "reading")] == ["Dracula"]


def test_unknown_shelf_is_rejected(reader, book):
    with pytest.raises(ValueError):
        set_shelf(reader.id, book.id, "abandoned")


def test_database_rejects_a_bad_status(reader, book):
    db.session.add(ShelfEntry(user_id=reader.id, book_id=book.id, status="abandoned"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_shelf_buttons_on_the_book_page(client, reader, book):
    log_in(client)
    shelve(client, book, "reading")
    html = client.get(f"/books/{book.id}").get_data(as_text=True)
    # the current shelf is pressed, and pressing it again sends "" to take it off
    assert 'value="" aria-pressed="true">Reading' in " ".join(html.split())


def test_pressing_the_current_shelf_takes_the_book_off(client, reader, book):
    log_in(client)
    shelve(client, book, "want")
    shelve(client, book, "")
    assert ShelfEntry.query.count() == 0


def test_shelf_form_needs_login_and_a_real_shelf(client, reader, book):
    assert "/login" in shelve(client, book, "want").headers["Location"]
    log_in(client)
    assert shelve(client, book, "abandoned").status_code == 400


def test_next_only_redirects_within_the_site(client, reader, book):
    log_in(client)
    ok = client.post(f"/books/{book.id}/shelf", data={"status": "finished", "next": "/"})
    evil = client.post(f"/books/{book.id}/shelf", data={"status": "want", "next": "https://evil.example"})
    assert ok.headers["Location"].endswith("/")
    assert "evil.example" not in evil.headers["Location"]


def test_seed_reset_keeps_shelved_books(app, reader, tmp_path, monkeypatch):
    csv_file = tmp_path / "books.csv"
    csv_file.write_text("title,author,genre\nDune,Frank Herbert,Sci-Fi\n", encoding="utf-8")
    monkeypatch.setattr(cli, "SEED_FILE", str(csv_file))
    monkeypatch.setattr(openlibrary, "best_match", lambda *args, **kwargs: {})
    shelved = Book(title="My Shelved Book", author="Somebody")
    db.session.add(shelved)
    db.session.commit()
    set_shelf(reader.id, shelved.id, "want")

    app.test_cli_runner().invoke(args=["seed", "--reset"], input="y\n")

    assert Book.query.filter_by(title="My Shelved Book").first() is not None
