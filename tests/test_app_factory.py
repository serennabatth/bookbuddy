"""Tests for create_app() and the blueprints."""
from app import create_app


def test_create_app_gives_a_new_app_each_time():
    first, second = create_app("testing"), create_app("testing")
    assert first is not second
    assert first.config["TESTING"]


def test_pages_kept_their_urls_after_moving_into_blueprints(app):
    urls = {rule.rule for rule in app.url_map.iter_rules()}
    for url in ["/", "/browse", "/top-rated", "/books/<int:book_id>", "/login", "/signup",
                "/add-review", "/favourites", "/history", "/profile", "/following",
                "/public/<handle>", "/settings", "/privacy"]:
        assert url in urls


def test_login_required_pages_send_you_to_login(client):
    response = client.get("/favourites")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
