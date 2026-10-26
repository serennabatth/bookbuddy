"""Tests for following and followers."""
import pytest
from sqlalchemy.exc import IntegrityError

from main import Follow, User, db


def make_user(email, handle, name="Reader"):
    user = User(email=email, handle=handle, name=name)
    user.set_password("long-password")
    db.session.add(user)
    db.session.commit()
    return user


def log_in(client, email):
    client.post("/login", data={"email": email, "password": "long-password"})


@pytest.fixture
def two_readers(app):
    return make_user("me@example.com", "@me", "Me"), make_user("them@example.com", "@them", "Them")


def toggle(client, user_id):
    return client.post("/api/following/toggle", json={"user_id": user_id})


def test_follow_then_unfollow(client, two_readers):
    me, them = two_readers
    log_in(client, "me@example.com")

    assert toggle(client, them.id).get_json()["state"] == "followed"
    assert Follow.query.filter_by(follower_id=me.id, followed_id=them.id).count() == 1

    assert toggle(client, them.id).get_json()["state"] == "unfollowed"
    assert Follow.query.count() == 0


def test_you_cannot_follow_yourself(client, two_readers):
    me, _ = two_readers
    log_in(client, "me@example.com")
    assert toggle(client, me.id).status_code == 400
    assert Follow.query.count() == 0


def test_the_database_blocks_self_follows_and_duplicates(app, two_readers):
    me, them = two_readers
    db.session.add(Follow(follower_id=me.id, followed_id=me.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    db.session.add_all([Follow(follower_id=me.id, followed_id=them.id),
                        Follow(follower_id=me.id, followed_id=them.id)])
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_follow_needs_a_login(client, two_readers):
    _, them = two_readers
    response = toggle(client, them.id)
    assert response.status_code == 302           # sent to the login page
    assert Follow.query.count() == 0


def test_lists_show_real_people(client, two_readers):
    me, them = two_readers
    db.session.add(Follow(follower_id=them.id, followed_id=me.id))   # they follow me
    db.session.commit()
    log_in(client, "me@example.com")

    assert "@them" in client.get("/followers").get_data(as_text=True)
    assert "@them" not in client.get("/following").get_data(as_text=True)


def test_remove_a_follower(client, two_readers):
    me, them = two_readers
    db.session.add(Follow(follower_id=them.id, followed_id=me.id))
    db.session.commit()
    log_in(client, "me@example.com")

    assert client.post("/api/followers/remove", json={"user_id": them.id}).get_json()["ok"] is True
    assert Follow.query.count() == 0


def test_profile_counts_and_follow_button(client, two_readers):
    me, them = two_readers
    log_in(client, "me@example.com")
    client.post(f"/users/{them.id}/follow")             # the button on their profile

    html = client.get("/public/them").get_data(as_text=True)
    assert 'aria-pressed="true"' in html                # the button now says you follow them
    assert Follow.query.filter_by(follower_id=me.id, followed_id=them.id).count() == 1
