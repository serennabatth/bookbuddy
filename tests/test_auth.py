"""Tests for signing up and logging in."""
from main import User


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
    client.get("/logout")

    response = client.post("/login", data={"email": "a@example.com", "password": "wrong"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")  # sent back, not let in
