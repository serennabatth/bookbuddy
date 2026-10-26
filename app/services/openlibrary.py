"""Open Library API - covers, book details, descriptions and search.

All requests go through get_json(), so tests only need to patch that one
function to stop real network calls.
"""
import json
from urllib.parse import quote
from urllib.request import Request, urlopen

API_URL = "https://openlibrary.org"
COVERS_URL = "https://covers.openlibrary.org/b"
USER_AGENT = "BookBuddy/1.0 (personal project)"


def get_json(url: str, timeout: int = 15):
    """GET a JSON response. Returns None if anything goes wrong."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def cover_url(cover_i: str = "", isbn: str = "", olid: str = "") -> str:
    """Build a cover image URL from whichever id we have (cover id > ISBN > edition id).

    ?default=false makes Open Library return a 404 instead of a blank image,
    so the page falls back to the coloured title tile.
    """
    cover_i = (cover_i or "").strip()
    isbn = (isbn or "").strip()
    olid = (olid or "").strip()

    if cover_i:
        return f"{COVERS_URL}/id/{cover_i}-L.jpg?default=false"
    if isbn:
        return f"{COVERS_URL}/isbn/{isbn}-L.jpg?default=false"
    if olid:
        return f"{COVERS_URL}/olid/{olid}-L.jpg?default=false"
    return ""


def _first(values) -> str:
    """First item of a list as a trimmed string, or ""."""
    if isinstance(values, list) and values:
        return str(values[0] or "").strip()
    return ""


def _score_search_result(doc: dict, title: str, author: str) -> int:
    """Score how well a search result matches the book we're looking for.

    Title and author count most. Then English editions and works with lots
    of editions get a boost, since that's usually the well-known version.
    """
    t_low, a_low = title.strip().lower(), author.strip().lower()
    score = 0

    d_title = (doc.get("title") or "").strip().lower()
    if d_title == t_low:
        score += 50
    elif t_low and t_low in d_title:
        score += 25

    first_author = _first(doc.get("author_name")).lower()
    if a_low and first_author:
        if first_author == a_low:
            score += 50
        elif a_low in first_author or first_author in a_low:
            score += 25

    if doc.get("cover_i"):
        score += 10
    if "eng" in (doc.get("language") or []):
        score += 20
    score += min(int(doc.get("edition_count") or 0), 100) // 5   # max +20

    return score


def pick_best_search_result(docs: list, title: str, author: str = "") -> dict | None:
    """The best matching search result, or None if the list is empty."""
    if not docs:
        return None
    return max(docs, key=lambda doc: _score_search_result(doc, title, author))


def _english_edition_cover(work_key: str) -> str:
    """Cover id from an English edition of the work, or "".

    Sometimes the default cover is from a translation (e.g. the French
    edition of A Brief History of Time).
    """
    if not (work_key or "").startswith("/works/"):
        return ""
    data = get_json(f"{API_URL}{work_key}/editions.json?limit=100") or {}
    for edition in data.get("entries") or []:
        languages = {lang.get("key") for lang in edition.get("languages") or []}
        covers = [c for c in edition.get("covers") or [] if isinstance(c, int) and c > 0]
        if "/languages/eng" in languages and covers:
            return str(covers[0])
    return ""


def best_match(title: str, author: str = "", timeout: int = 15) -> dict:
    """Look up a book. Returns {cover, cover_i, isbn, olid, year}, or {} if not found."""
    t = (title or "").strip()
    a = (author or "").strip()
    if not t:
        return {}

    fields = "key,title,author_name,cover_i,isbn,edition_key,first_publish_year,language,edition_count"
    q = f"{t} {a}".strip()
    data = get_json(f"{API_URL}/search.json?q={quote(q)}&limit=20&fields={fields}", timeout=timeout) or {}
    best = pick_best_search_result(data.get("docs") or [], t, a)
    if best is None:
        return {}

    cover_i = _english_edition_cover(best.get("key") or "") or str(best.get("cover_i") or "").strip()
    olid = _first(best.get("edition_key"))
    isbn = _first(best.get("isbn"))
    year = str(best["first_publish_year"]) if isinstance(best.get("first_publish_year"), int) else ""

    return {
        "cover": cover_url(cover_i=cover_i, isbn=isbn, olid=olid),
        "cover_i": cover_i,
        "isbn": isbn,
        "olid": olid,
        "year": year,
    }


def description_for(title: str, author: str = "", timeout: int = 4) -> str | None:
    """Short description of a book.

    Returns "" if Open Library doesn't have one, or None if it couldn't be
    reached (so we can try again next time).
    """
    t = (title or "").strip()
    a = (author or "").strip()
    if not t:
        return ""

    q = f"{t} {a}".strip()
    data = get_json(f"{API_URL}/search.json?q={quote(q)}&limit=5", timeout=timeout)
    if data is None:
        return None

    best = pick_best_search_result(data.get("docs") or [], t, a) or {}
    work_key = str(best.get("key") or "").strip()
    if not work_key.startswith("/works/"):
        return ""

    work = get_json(f"{API_URL}{work_key}.json", timeout=timeout)
    if work is None:
        return None

    desc = work.get("description")
    if isinstance(desc, dict):   # sometimes it's {"type": ..., "value": "..."}
        desc = desc.get("value")
    if not isinstance(desc, str):
        return ""

    desc = desc.strip()
    if len(desc) > 600:   # cut at a word so it fits the page
        desc = desc[:600].rsplit(" ", 1)[0].rstrip() + "…"
    return desc


def search(q: str, limit: int = 10) -> list[dict]:
    """Search Open Library for books (used by the Add Book page)."""
    data = get_json(f"{API_URL}/search.json?q={quote(q)}&limit={limit}", timeout=10)
    if not data:
        return []

    results = []
    for d in data.get("docs") or []:
        title = (d.get("title") or "").strip()
        author = _first(d.get("author_name"))
        if not (title and author):
            continue
        cover_i = str(d.get("cover_i") or "").strip()
        isbn = _first(d.get("isbn"))
        olid = _first(d.get("edition_key"))
        results.append({
            "title": title,
            "author": author,
            "year": str(d["first_publish_year"]) if isinstance(d.get("first_publish_year"), int) else "",
            "cover": cover_url(cover_i=cover_i, isbn=isbn, olid=olid),
            "cover_i": cover_i,
            "isbn": isbn,
            "olid": olid,
        })
    return results
