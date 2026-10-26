"""Shared test setup."""
import pytest

from app import create_app
from app.extensions import db
from app.services import openlibrary


@pytest.fixture
def app():
    """A new app with an empty in-memory database for each test.

    CSRF is turned off so tests can post forms directly
    (test_csrf.py turns it back on to check it works).
    """
    app = create_app("testing")
    app.config.update(WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        yield app
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
