"""Tests for signing up and logging in."""
from app.models import User


def test_login_page_loads(client):
    response = client.get("/login")
    assert response.status_code == 200


def test_signup_stores_a_hashed_password(client):
    client.post("/signup", data={
        "email": "Reader@Example.com",
        "password": "correct-horse",
        "name": "Test Reader",
        "handle": "testreader",
    })

    user = User.query.filter_by(email="reader@example.com").first()
    assert user is not None                        # email saved in lower case
    assert user.password_hash != "correct-horse"    # never stored as plain text
    assert user.check_password("correct-horse")
    assert user.handle == "@testreader"


def test_login_with_wrong_password_is_rejected(client):
    client.post("/signup", data={"email": "a@example.com", "password": "right-password"})
    client.post("/logout")

    response = client.post("/login", data={"email": "a@example.com", "password": "wrong"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")  # sent back, not let in


def test_logout_only_accepts_post(client):
    # A GET logout could be triggered by any image or link on another site.
    assert client.get("/logout").status_code == 405


def test_login_form_fields_match_what_the_login_route_reads(client):
    # The page's form must send "email" and "password", the names login() reads.
    # A mismatch here once made every login fail with "Invalid email or password".
    html = client.get("/login").get_data(as_text=True)
    assert 'name="email"' in html
    assert 'name="password"' in html


def test_login_with_correct_password_succeeds(client):
    client.post("/signup", data={"email": "b@example.com", "password": "right-password"})
    client.post("/logout")

    response = client.post("/login", data={"email": "b@example.com", "password": "right-password"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")  # sent to the home page
