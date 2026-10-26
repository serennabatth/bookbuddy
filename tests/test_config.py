"""Tests for app/config.py."""
import pytest
from flask import Flask

from app.config import DevelopmentConfig, ProductionConfig, TestingConfig, get_config


def test_get_config_by_name():
    assert get_config("development") is DevelopmentConfig
    assert get_config("testing") is TestingConfig
    assert get_config("Production") is ProductionConfig   # not case sensitive


def test_typo_in_env_name_raises_error():
    with pytest.raises(ValueError):
        get_config("prod")


def test_production_needs_secret_key():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = None
    with pytest.raises(RuntimeError):
        ProductionConfig.init_app(app)


def test_development_uses_random_key_if_missing():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = None
    DevelopmentConfig.init_app(app)
    assert len(app.config["SECRET_KEY"]) == 64


def test_production_cookies_https_only():
    assert ProductionConfig.SESSION_COOKIE_SECURE
    assert ProductionConfig.REMEMBER_COOKIE_SECURE
    assert not DevelopmentConfig.SESSION_COOKIE_SECURE   # localhost is http


def test_tests_use_in_memory_db(app):
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite://"
    assert app.config["RATE_LIMITS_ENABLED"] is False
