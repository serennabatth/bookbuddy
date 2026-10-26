"""Tests for app/services/openlibrary.py, using fake API responses."""
from app.services import openlibrary


def fake_api(monkeypatch, responses):
    """Make get_json return canned data for any URL containing one of the keys."""
    def fake_get_json(url, timeout=15):
        for part, data in responses.items():
            if part in url:
                return data
        return None
    monkeypatch.setattr(openlibrary, "get_json", fake_get_json)


def test_cover_url_uses_best_id_available():
    assert "/id/1-L.jpg" in openlibrary.cover_url(cover_i="1", isbn="2")
    assert "/isbn/2-L.jpg" in openlibrary.cover_url(isbn="2")
    assert openlibrary.cover_url() == ""


def test_description_comes_from_best_matching_work(monkeypatch):
    fake_api(monkeypatch, {
        "search.json": {"docs": [
            {"key": "/works/OL1W", "title": "Dracula's Guest", "author_name": ["Bram Stoker"]},
            {"key": "/works/OL2W", "title": "Dracula", "author_name": ["Bram Stoker"]},
        ]},
        "/works/OL2W.json": {"description": {"value": "A vampire story."}},
    })
    assert openlibrary.description_for("Dracula", "Bram Stoker") == "A vampire story."


def test_description_is_none_when_offline():
    # conftest blocks the network, so get_json always returns None here
    assert openlibrary.description_for("Dracula", "Bram Stoker") is None


def test_description_is_empty_when_no_results(monkeypatch):
    fake_api(monkeypatch, {"search.json": {"docs": []}})
    assert openlibrary.description_for("Not A Real Book") == ""


def test_long_descriptions_are_cut_at_a_word(monkeypatch):
    fake_api(monkeypatch, {
        "search.json": {"docs": [{"key": "/works/OL1W", "title": "Long", "author_name": ["A"]}]},
        "/works/OL1W.json": {"description": "word " * 300},
    })
    desc = openlibrary.description_for("Long", "A")
    assert desc.endswith("word…")
    assert len(desc) <= 601


def test_search_skips_results_without_an_author(monkeypatch):
    fake_api(monkeypatch, {"search.json": {"docs": [
        {"title": "Dune", "author_name": ["Frank Herbert"], "cover_i": 5, "first_publish_year": 1965},
        {"title": "Mystery pamphlet"},
    ]}})
    results = openlibrary.search("dune")
    assert len(results) == 1
    assert results[0]["year"] == "1965"
    assert "/id/5-L.jpg" in results[0]["cover"]


def test_openlibrary_search_api_route(client, monkeypatch):
    fake_api(monkeypatch, {"search.json": {"docs": [{"title": "Dune", "author_name": ["Frank Herbert"]}]}})
    data = client.get("/api/openlibrary?q=dune").get_json()
    assert [book["title"] for book in data] == ["Dune"]
