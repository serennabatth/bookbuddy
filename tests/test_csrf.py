"""Tests that requests without a valid CSRF token are refused."""
import re

import pytest

from main import User


@pytest.fixture
def csrf_client(app):
    """A test client with CSRF protection switched on, as in the real app."""
    app.config["WTF_CSRF_ENABLED"] = True
    yield app.test_client()
    app.config["WTF_CSRF_ENABLED"] = False


def get_token(client, path):
    """Load a page and pull the hidden csrf_token value out of its HTML."""
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_form_without_token_is_refused(csrf_client):
    csrf_client.post("/signup", data={"email": "x@example.com", "password": "long-password"})
    assert User.query.filter_by(email="x@example.com").first() is None


def test_form_with_token_is_accepted(csrf_client):
    token = get_token(csrf_client, "/signup")
    csrf_client.post("/signup", data={
        "csrf_token": token, "email": "x@example.com", "password": "long-password",
    })
    assert User.query.filter_by(email="x@example.com").first() is not None


def test_api_call_without_token_is_refused(csrf_client):
    response = csrf_client.post("/api/following/toggle", json={"handle": "@someone"})
    assert response.status_code == 400
    assert response.get_json()["ok"] is False
