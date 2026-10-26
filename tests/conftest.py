"""Shared test setup.

These settings are applied before main.py is imported, so the tests use a
throwaway in-memory database and never touch instance/bookbuddy.db.
"""
import os

os.environ["BOOKBUDDY_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory SQLite, gone after each run

import pytest  # noqa: E402

from main import app as flask_app, db  # noqa: E402


@pytest.fixture
def app():
    """A fresh, empty database for every test.

    CSRF checks are switched off here so tests can post forms directly.
    tests/test_csrf.py switches them back on to prove they work.
    """
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """A fake browser that sends requests to the app without a server."""
    return app.test_client()
