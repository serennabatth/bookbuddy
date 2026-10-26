"""Users - handles, display names and follows."""
import re

from app.extensions import db
from app.models import Follow, User

HANDLE_PATTERN = re.compile(r"[a-z0-9_.]{3,30}")
HANDLE_RULES = "Handles are 3-30 characters: letters, numbers, dots and underscores."


def normalize_handle(raw: str) -> str | None:
    """Clean up a typed handle, e.g. " Alex.Morgan " -> "@alex.morgan".

    Returns None if it breaks the rules (3-30 chars, letters/numbers/dots/underscores).
    """
    body = (raw or "").strip().lstrip("@").lower()
    return f"@{body}" if HANDLE_PATTERN.fullmatch(body) else None


def handle_taken(handle: str, except_user_id: int | None = None) -> bool:
    """True if another account already has this handle."""
    query = User.query.filter_by(handle=handle)
    if except_user_id is not None:
        query = query.filter(User.id != except_user_id)
    return query.first() is not None


def display_name(user: User | None) -> str:
    """Name shown next to a review: handle, then name, then "reader"."""
    if not user:
        return "reader"
    if user.is_private:
        return "A reader"   # don't show who private users are
    return (user.handle or user.name or "").strip() or "reader"


# --- follows ---

def is_following(follower_id: int, followed_id: int) -> bool:
    return Follow.query.filter_by(follower_id=follower_id, followed_id=followed_id).first() is not None


def follow_counts(user_id: int) -> tuple[int, int]:
    """Returns (followers, following) for a user."""
    followers = Follow.query.filter_by(followed_id=user_id).count()
    following = Follow.query.filter_by(follower_id=user_id).count()
    return followers, following


def toggle_follow(follower: User, target: User) -> bool:
    """Follow or unfollow. Returns True if now following."""
    existing = Follow.query.filter_by(follower_id=follower.id, followed_id=target.id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return False
    db.session.add(Follow(follower_id=follower.id, followed_id=target.id))
    db.session.commit()
    return True
