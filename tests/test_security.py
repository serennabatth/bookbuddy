"""Tests for account security: passwords, reset links, sign-in limits, redirects and privacy."""
import pytest

import main
from main import Book, Review, User, db, generate_reset_token, rate_limiter, safe_next_url


@pytest.fixture(autouse=True)
def no_open_library(monkeypatch):
    monkeypatch.setattr(main, "_ol_description_for", lambda *args, **kwargs: "")


def make_user(email="reader@example.com", password="long-password", handle=None):
    user = User(email=email, handle=handle)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def test_sign_up_rejects_short_passwords(client, app):
    client.post("/signup", data={"email": "short@example.com", "password": "seven77"})
    assert User.query.filter_by(email="short@example.com").first() is None


def test_a_reset_link_works_once(client, app):
    user = make_user()
    token = generate_reset_token(user)
    form = {"new_password": "brand-new-password", "confirm_password": "brand-new-password"}

    client.post(f"/reset-password/{token}", data=form)
    assert db.session.get(User, user.id).check_password("brand-new-password")

    second = client.post(f"/reset-password/{token}", data={**form, "new_password": "x" * 12, "confirm_password": "x" * 12})
    assert "/forgot-password" in second.headers["Location"]       # link no longer works
    assert db.session.get(User, user.id).check_password("brand-new-password")


def test_a_reset_link_stops_working_after_a_password_change(client, app):
    user = make_user()
    token = generate_reset_token(user)
    user.set_password("changed-elsewhere")
    db.session.commit()
    assert "/forgot-password" in client.get(f"/reset-password/{token}").headers["Location"]


def test_too_many_sign_in_attempts_are_slowed_down(client, app):
    make_user()
    app.config["RATE_LIMITS_ENABLED"] = True
    rate_limiter.reset()
    try:
        for _ in range(5):
            client.post("/login", data={"email": "reader@example.com", "password": "wrong"})
        sixth = client.post("/login", data={"email": "reader@example.com", "password": "long-password"})
        assert sixth.status_code == 429          # blocked, even with the right password
    finally:
        app.config["RATE_LIMITS_ENABLED"] = False
        rate_limiter.reset()


@pytest.mark.parametrize("target, expected", [
    ("/books/3", "/books/3"),
    ("https://evil.example/", None),
    ("//evil.example/", None),
    ("", None),
])
def test_only_redirect_to_pages_on_this_site(target, expected):
    assert safe_next_url(target) == expected


def test_login_returns_you_to_the_page_you_wanted(client, app):
    make_user()
    response = client.post("/login?next=/favourites", data={"email": "reader@example.com", "password": "long-password"})
    assert response.headers["Location"].endswith("/favourites")


def test_remember_me_is_your_choice(client, app):
    make_user()
    plain = client.post("/login", data={"email": "reader@example.com", "password": "long-password"})
    assert "remember_token" not in " ".join(plain.headers.getlist("Set-Cookie"))
    client.post("/logout")
    remembered = client.post("/login", data={"email": "reader@example.com", "password": "long-password", "remember": "1"})
    assert "remember_token" in " ".join(remembered.headers.getlist("Set-Cookie"))


def test_private_profiles_hide_reviews_and_names(client, app):
    owner = make_user("owner@example.com", handle="@quietreader")
    book = Book(title="Dracula", author="Bram Stoker")
    db.session.add(book)
    db.session.commit()
    db.session.add(Review(user_id=owner.id, book_id=book.id, book_title=book.title, rating=4, text="Secret thoughts"))
    owner.is_private = True
    db.session.commit()

    profile = client.get("/public/quietreader").get_data(as_text=True)
    assert "This profile is private" in profile and "Secret thoughts" not in profile

    book_page = client.get(f"/books/{book.id}").get_data(as_text=True)
    assert "A reader" in book_page and "@quietreader" not in book_page


def test_the_privacy_setting_is_saved_on_the_account(client, app):
    make_user()
    client.post("/login", data={"email": "reader@example.com", "password": "long-password"})
    client.post("/privacy", data={"profile_private": "on"})
    assert User.query.filter_by(email="reader@example.com").one().is_private is True
