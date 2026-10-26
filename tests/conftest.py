"""Shared test setup.

These env vars are set before main.py is imported so the tests use an
in-memory database and never touch instance/bookbuddy.db.
"""
import os

os.environ["BOOKBUDDY_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory SQLite

import pytest  # noqa: E402

from main import app as flask_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.services import openlibrary  # noqa: E402


@pytest.fixture
def app():
    """Fresh empty database for each test.

    CSRF is turned off so tests can post forms directly
    (test_csrf.py turns it back on to check it works).
    """
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Test client - sends requests to the app without running a server."""
    return app.test_client()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Block real Open Library requests in every test."""
    monkeypatch.setattr(openlibrary, "get_json", lambda *args, **kwargs: None)
