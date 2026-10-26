"""Tests for handles and profile pictures."""
import os
from io import BytesIO

import pytest
from PIL import Image

from main import User, normalize_handle


def sign_up(client, email, handle=""):
    return client.post("/signup", data={"email": email, "password": "long-password", "handle": handle})


@pytest.mark.parametrize("typed, stored", [
    ("alexmorgan", "@alexmorgan"),
    ("@Alex.Morgan ", "@alex.morgan"),   # @ optional, stored in lower case, spaces trimmed
    ("al", None),                         # too short
    ("alex morgan", None),                # spaces not allowed
    ("alex<script>", None),               # symbols not allowed
])
def test_normalize_handle(typed, stored):
    assert normalize_handle(typed) == stored


def test_two_people_cannot_share_a_handle(client):
    sign_up(client, "first@example.com", "bookworm")
    client.post("/logout")
    sign_up(client, "second@example.com", "BookWorm")   # same handle, different case

    assert User.query.filter_by(email="second@example.com").first() is None


def test_signing_up_without_a_handle_is_allowed(client):
    sign_up(client, "nohandle@example.com")
    user = User.query.filter_by(email="nohandle@example.com").first()
    assert user is not None
    assert user.handle is None


def test_cannot_change_your_handle_to_someone_elses(client):
    sign_up(client, "first@example.com", "bookworm")
    client.post("/logout")
    sign_up(client, "second@example.com", "reader2")

    client.post("/edit-profile", data={"name": "Second", "handle": "bookworm", "bio": ""})

    assert User.query.filter_by(email="second@example.com").first().handle == "@reader2"


def test_profile_picture_is_saved(client, app, tmp_path, monkeypatch):
    # Save uploads in a temporary folder so the test never touches the real static/.
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    sign_up(client, "pic@example.com", "picperson")

    image = BytesIO()
    Image.new("RGB", (8, 8), "teal").save(image, "PNG")
    image.seek(0)
    client.post(
        "/edit-profile",
        data={"name": "Pic", "handle": "picperson", "bio": "", "avatar": (image, "me.png")},
        content_type="multipart/form-data",
    )

    user = User.query.filter_by(email="pic@example.com").first()
    assert user.avatar.startswith("uploads/avatars/user_")       # the path is stored now
    assert os.path.exists(os.path.join(tmp_path, user.avatar))   # and the file exists


def test_public_profile_shows_the_owner_not_the_viewer(client):
    sign_up(client, "owner@example.com", "theowner")
    client.post("/edit-profile", data={"name": "Owner Name", "handle": "theowner", "bio": ""})
    client.post("/logout")

    html = client.get("/public/theowner").get_data(as_text=True)
    assert "Owner Name" in html
