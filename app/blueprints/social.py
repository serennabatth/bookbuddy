"""Profiles, following and sharing."""
from io import BytesIO

import qrcode
import qrcode.image.pil  # noqa: F401 - needed for PNG output
from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Follow, Review, User
from app.services.catalogue import average_rating, book_dict
from app.services.users import follow_counts, is_following, toggle_follow

bp = Blueprint("social", __name__)


def current_user_name() -> str:
    if current_user.is_authenticated and current_user.name:
        return current_user.name
    return ""


def current_user_handle() -> str:
    if current_user.is_authenticated and current_user.handle:
        return current_user.handle
    return ""


def _people(users, q: str) -> list[dict]:
    """Format users for the followers/following pages, with optional search."""
    q = (q or "").strip().lower()
    people = []
    for u in users:
        name, handle = (u.name or ""), (u.handle or "")
        if q and q not in name.lower() and q not in handle.lower():
            continue
        people.append({"id": u.id, "name": name or "Reader", "handle": handle, "avatar": u.avatar or ""})
    return people


@bp.route("/following")
@login_required
def following():
    users = (
        db.session.execute(
            db.select(User).join(Follow, Follow.followed_id == User.id)
            .where(Follow.follower_id == current_user.id)
            .order_by(Follow.created_at.desc())
        ).scalars().all()
    )
    return render_template("following.html", people=_people(users, request.args.get("q")))


@bp.route("/followers")
@login_required
def followers():
    users = (
        db.session.execute(
            db.select(User).join(Follow, Follow.follower_id == User.id)
            .where(Follow.followed_id == current_user.id)
            .order_by(Follow.created_at.desc())
        ).scalars().all()
    )
    return render_template("followers.html", people=_people(users, request.args.get("q")))


def _target_user_from_json():
    """Get the user from {"user_id": ...} in the request body, or None."""
    data = request.get_json(silent=True) or {}
    try:
        return db.session.get(User, int(data.get("user_id") or 0))
    except (TypeError, ValueError):
        return None


@bp.post("/api/following/toggle")
@login_required
def api_following_toggle():
    target = _target_user_from_json()
    if target is None:
        return jsonify({"ok": False, "error": "That reader doesn't exist."}), 404
    if target.id == current_user.id:
        return jsonify({"ok": False, "error": "You can't follow yourself."}), 400
    now_following = toggle_follow(current_user, target)
    return jsonify({"ok": True, "state": "followed" if now_following else "unfollowed"})


@bp.post("/api/followers/remove")
@login_required
def api_followers_remove():
    target = _target_user_from_json()
    row = target and Follow.query.filter_by(follower_id=target.id, followed_id=current_user.id).first()
    if not row:
        return jsonify({"ok": False, "error": "That reader doesn't follow you."}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/users/<int:user_id>/follow")
@login_required
def follow_toggle_form(user_id):
    """Follow/unfollow button on public profiles (plain form, no JS needed)."""
    target = db.get_or_404(User, user_id)
    if target.id != current_user.id:
        toggle_follow(current_user, target)
    if target.handle:
        return redirect(url_for("social.public_profile", handle=target.handle.lstrip("@")))
    return redirect(url_for("social.profile_page"))


@bp.route("/profile")
@login_required
def profile_page():
    user = {
        "name": current_user.name or "",
        "handle": current_user.handle or "",
        "bio": current_user.bio or "",
        "avatar": current_user.avatar or "",
    }

    recent = (
        Review.query
        .filter_by(user_id=current_user.id)
        .order_by(Review.created.desc())
        .limit(6)
        .all()
    )

    followers_count, following_count = follow_counts(current_user.id)
    return render_template(
        "profile.html", user=user, your_reviews=recent, is_own=True,
        review_count=Review.query.filter_by(user_id=current_user.id).count(),
        followers_count=followers_count, following_count=following_count,
    )


@bp.route("/share-profile")
def share_profile():
    user = {"name": current_user_name(), "handle": current_user_handle()}
    books = []
    if current_user.is_authenticated:
        reviewed = (
            Review.query.filter_by(user_id=current_user.id)
            .order_by(Review.created.desc()).limit(10).all()
        )
        books = [book_dict(r.book, average_rating(r.book_id)) for r in reviewed]
    return render_template("share_profile.html", user=user, books=books)


@bp.route("/qr-profile")
def qr_profile():
    # only allow QR codes for links to this site
    url = request.args.get("url") or ""
    if not url.startswith(request.host_url):
        handle = current_user_handle().lstrip("@")
        if not handle:
            abort(404)
        url = url_for("social.public_profile", handle=handle, _external=True)

    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@bp.route("/public/<handle>")
def public_profile(handle):
    owner = User.query.filter_by(handle=f"@{handle.lstrip('@').lower()}").first_or_404()
    user = {
        "name": owner.name or "", "handle": owner.handle or "",
        "bio": owner.bio or "", "avatar": owner.avatar or "",
    }
    reviews = (
        Review.query.filter_by(user_id=owner.id)
        .order_by(Review.created.desc()).limit(6).all()
    )
    followers_count, following_count = follow_counts(owner.id)
    is_own = current_user.is_authenticated and current_user.id == owner.id
    hidden = owner.is_private and not is_own
    if hidden:
        reviews = []
    return render_template(
        "profile.html", user=user, your_reviews=reviews, is_private_view=hidden,
        profile_id=owner.id, is_own=is_own,
        is_following=current_user.is_authenticated and not is_own and is_following(current_user.id, owner.id),
        review_count=Review.query.filter_by(user_id=owner.id).count(),
        followers_count=followers_count, following_count=following_count,
    )
