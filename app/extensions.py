"""Flask extensions.

Created here without an app and hooked up later with init_app(). This way
models.py can import db without importing the app (avoids circular imports).
"""
import sqlite3

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()

# Batch mode is needed for SQLite - it can't alter most columns directly,
# so Alembic rebuilds the table instead
migrate = Migrate(render_as_batch=True)

# CSRF tokens on every form/POST request (see base.html for the fetch header)
csrf = CSRFProtect()

login_manager = LoginManager()
login_manager.login_view = "login"  # redirect here if login is required


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """Turn on foreign key checks for SQLite (they're off by default).

    Postgres always enforces them, so this is just for local dev.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
