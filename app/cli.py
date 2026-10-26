"""Command line tasks, e.g. `flask seed`."""
import csv
import os

import click
from flask.cli import with_appcontext

from app.config import BASE_DIR
from app.extensions import db
from app.models import Book, Favourite, History, Review, ShelfEntry
from app.services import openlibrary
from app.services.catalogue import PLACEHOLDER_COVER
from app.services.security import safe_cover_url

SEED_FILE = os.path.join(BASE_DIR, "data", "books.csv")


def _is_referenced(book_id: int) -> bool:
    """True if the book has any reviews, favourites, history or shelf entries."""
    return any(
        model.query.filter_by(book_id=book_id).first() is not None
        for model in (Review, Favourite, History, ShelfEntry)
    )


def _apply_metadata(book: Book, meta: dict) -> None:
    """Copy Open Library details onto a book (keeps the existing year if it has one)."""
    book.year = book.year or (meta.get("year") or "").strip()
    book.cover_i = (meta.get("cover_i") or "").strip()
    book.isbn = (meta.get("isbn") or "").strip()
    book.olid = (meta.get("olid") or "").strip()
    book.cover = safe_cover_url(meta.get("cover")) or PLACEHOLDER_COVER


def _metadata_for(title: str, author: str, cover_id: str = "") -> dict:
    """Get Open Library details. A cover_id from books.csv overrides the cover."""
    meta = openlibrary.best_match(title, author) or {}
    if cover_id:
        meta["cover_i"] = cover_id
        meta["cover"] = openlibrary.cover_url(cover_i=cover_id)
    return meta


@click.command("seed")
@with_appcontext
@click.option("--reset", is_flag=True,
              help="Also delete books that aren't in data/books.csv and that nobody has used.")
@click.option("--refresh-covers", is_flag=True,
              help="Look up covers again for curated books that are already saved.")
def seed_command(reset: bool, refresh_covers: bool) -> None:
    """Add the books from data/books.csv (needs internet for covers).

    flask seed                    add any missing books
    flask seed --reset            also remove unused books that aren't in the csv
    flask seed --refresh-covers   look up covers again

    The cover_id column in books.csv can be used to pick a cover manually
    if the automatic match is wrong.
    """
    with open(SEED_FILE, newline="", encoding="utf-8") as f:
        curated = [
            (r["title"].strip(), r["author"].strip(), r["genre"].strip(), (r.get("cover_id") or "").strip())
            for r in csv.DictReader(f)
        ]
    curated_keys = {(title, author) for title, author, _, _ in curated}

    if reset:
        unused = [
            b for b in Book.query.all()
            if (b.title, b.author) not in curated_keys and not _is_referenced(b.id)
        ]
        click.confirm(f"Delete {len(unused)} books that aren't curated and nobody has used?", abort=True)
        for b in unused:
            db.session.delete(b)
        db.session.commit()
        click.echo(f"Deleted {len(unused)} unused books.")

    added = updated = refreshed = 0
    for i, (title, author, genre, cover_id) in enumerate(curated, start=1):
        book = Book.query.filter_by(title=title, author=author).first()

        if book is None:
            book = Book(title=title, author=author, genre=genre)
            _apply_metadata(book, _metadata_for(title, author, cover_id))
            db.session.add(book)
            added += 1
            click.echo(f"[{i}/{len(curated)}] added {title}")
        else:
            if book.genre != genre:
                book.genre = genre  # fix genres from the old seed data
                updated += 1
            if refresh_covers or (cover_id and book.cover_i != cover_id):
                _apply_metadata(book, _metadata_for(title, author, cover_id))
                refreshed += 1
                click.echo(f"[{i}/{len(curated)}] new cover for {title}")
        db.session.commit()  # commit each book so nothing is lost if the connection drops

    click.echo(f"Added {added} books, fixed the genre of {updated}, refreshed {refreshed} covers. "
               f"The catalogue now has {Book.query.count()} books.")
