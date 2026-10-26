"""App settings for each environment.

Set BOOKBUDDY_ENV in .env to development (default), testing or production.
"""
import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv

# Load .env first so the settings below can see it.
# Anything already set in the real environment takes priority.
load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DEFAULT_DATABASE = "sqlite:///" + os.path.join(BASE_DIR, "instance", "bookbuddy.db")


def _env(name: str) -> str:
    """Get an environment variable, trimmed. Returns "" if it's missing."""
    return (os.environ.get(name) or "").strip()


class Config:
    """Base settings - the other configs inherit from this."""

    # Used to sign session cookies and password reset links, so keep it secret
    SECRET_KEY = _env("SECRET_KEY") or _env("BOOKBUDDY_SECRET") or None

    SQLALCHEMY_DATABASE_URI = _env("DATABASE_URL") or DEFAULT_DATABASE
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Cookie security: hidden from JavaScript and not sent on cross-site requests
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = False
    REMEMBER_COOKIE_DURATION = timedelta(days=30)

    # 2 MB upload limit (anything bigger gets a 413)
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    RATE_LIMITS_ENABLED = True

    @classmethod
    def init_app(cls, app) -> None:
        """If no secret key is set, use a random one so local dev still works."""
        if not app.config.get("SECRET_KEY"):
            app.logger.warning(
                "SECRET_KEY is not set, so a temporary random key is being used and "
                "everyone is logged out when the server restarts. Set SECRET_KEY in .env."
            )
            app.config["SECRET_KEY"] = secrets.token_hex(32)


class DevelopmentConfig(Config):
    """Local development."""


class TestingConfig(Config):
    """Used by pytest."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # in-memory db so tests don't touch real data
    RATE_LIMITS_ENABLED = False


class ProductionConfig(Config):
    """The live site (HTTPS)."""

    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True

    @classmethod
    def init_app(cls, app) -> None:
        """Don't start without a real secret key - otherwise login cookies could be faked."""
        if not app.config.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY must be set when BOOKBUDDY_ENV=production.")


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None) -> type[Config]:
    """Return the config class by name, falling back to BOOKBUDDY_ENV."""
    name = (name or _env("BOOKBUDDY_ENV") or "development").lower()
    try:
        return CONFIGS[name]
    except KeyError:
        raise ValueError(
            f"BOOKBUDDY_ENV must be one of {', '.join(CONFIGS)}, not {name!r}."
        ) from None
