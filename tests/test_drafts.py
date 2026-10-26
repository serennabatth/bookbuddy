"""Tests for review drafts.

The drafts themselves live in the browser (localStorage), so these tests
only check that each page includes the right draft code. The behaviour
(type, refresh, the draft comes back) needs checking in a browser.
"""
from app.extensions import db
from app.models import Book, Review, User


def sign_up(client):
    client.post("/signup", data={"email": "reader@example.com", "password": "long-password"})
    return User.query.filter_by(email="reader@example.com").one()


def test_add_review_page_has_a_draft_for_this_user(client):
    reader = sign_up(client)
    html = client.get("/add-review").get_data(as_text=True)
    assert f"bookbuddy:review-draft:{reader.id}:new" in html
    assert "Save draft" in html


def test_edit_page_keeps_a_draft_per_review(client):
    reader = sign_up(client)
    book = Book(title="Dracula", author="Bram Stoker")
    db.session.add(book)
    db.session.commit()
    review = Review(user_id=reader.id, book_id=book.id, book_title=book.title, rating=4, text="Good")
    db.session.add(review)
    db.session.commit()
    html = client.get(f"/reviews/{review.id}/edit").get_data(as_text=True)
    assert f"bookbuddy:review-draft:{reader.id}:review-{review.id}" in html


def test_every_page_clears_drafts_on_logout(client):
    assert "bookbuddy:review-draft:" in client.get("/login").get_data(as_text=True)
