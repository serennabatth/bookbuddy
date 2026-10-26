"""Tests for dates and "time ago" text."""
from datetime import timedelta

from app.blueprints.books import _time_ago
from app.models import utc_now


def test_utc_now_has_no_timezone_like_the_database():
    assert utc_now().tzinfo is None


def test_time_ago():
    assert _time_ago(utc_now()) == "just now"
    assert _time_ago(utc_now() - timedelta(hours=3)) == "3h ago"
    assert _time_ago(utc_now() - timedelta(days=2)) == "2d ago"
    assert _time_ago(None) == ""
