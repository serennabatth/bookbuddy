"""Security helpers - passwords, reset links, rate limiting and URL checks."""
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from flask import current_app, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.models import User

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def password_problem(password: str) -> str | None:
    """Return an error message if the password isn't allowed, otherwise None.

    Just a length rule (8-128) - NCSC/NIST guidance says length matters more
    than forcing symbols.
    """
    if len(password or "") < PASSWORD_MIN_LENGTH:
        return f"Passwords must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Passwords can be at most {PASSWORD_MAX_LENGTH} characters."
    return None


# --- password reset links ---

def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def generate_reset_token(user: User) -> str:
    """Make a signed token for a reset link.

    Includes the end of the current password hash, so once the password is
    changed the old link stops working (one use only).
    """
    return _reset_serializer().dumps({"email": user.email, "pw": user.password_hash[-16:]})


def verify_reset_token(token: str, max_age_seconds: int = 3600) -> User | None:
    """Return the user for a reset token, or None if it's invalid, expired or used."""
    try:
        data = _reset_serializer().loads(token, max_age=max_age_seconds)
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(data, dict):
        return None   # old format from before tokens were single use
    user = User.query.filter_by(email=data.get("email")).first()
    if user is None or user.password_hash[-16:] != data.get("pw"):
        return None
    return user


# --- rate limiting ---

class RateLimiter:
    """Allow at most `limit` attempts per `window` seconds for each key (e.g. an IP).

    Sliding window - old attempts drop off as time passes. Stored in memory
    so it resets on restart (a bigger site would use Redis).
    """

    def __init__(self) -> None:
        self._attempts: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and now - attempts[0] > window:
                attempts.popleft()
            if len(attempts) >= limit:
                return False
            attempts.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()


rate_limiter = RateLimiter()


def rate_limited(action: str, limit: int, window: float) -> bool:
    """True if this IP has tried `action` too many times recently."""
    if not current_app.config.get("RATE_LIMITS_ENABLED", True):
        return False
    return not rate_limiter.allow(f"{action}:{request.remote_addr}", limit, window)


# --- URL checks ---

def safe_next_url(target: str | None) -> str | None:
    """Only allow redirects to pages on this site (stops open redirects).

    e.g. /login?next=https://evil.example would otherwise send people to
    another site after logging in.
    """
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


def safe_cover_url(url: str) -> str:
    """Return the URL if it's a normal https link, otherwise "".

    Cover links can come from users, so things like javascript: or data:
    links, or anything with quotes/angle brackets, get thrown away.
    """
    url = (url or "").strip()
    if not url or any(ch in url for ch in "\"'<> "):
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return url
