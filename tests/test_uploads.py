"""Tests for profile picture uploads."""
import os
from io import BytesIO

import pytest
from PIL import Image

from app.models import User


@pytest.fixture
def logged_in(client, app, tmp_path, monkeypatch):
    """Log a reader in, and keep every upload inside a temporary folder."""
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    client.post("/signup", data={"email": "pic@example.com", "password": "long-password", "handle": "picperson"})
    return tmp_path


def upload(client, data: bytes, filename: str):
    return client.post(
        "/edit-profile",
        data={"name": "Pic", "handle": "picperson", "bio": "", "avatar": (BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def png_bytes(size=(800, 600)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, "teal").save(buf, "PNG")
    return buf.getvalue()


def avatar():
    return User.query.filter_by(email="pic@example.com").one().avatar


def test_a_real_picture_is_saved_as_a_small_clean_jpeg(client, logged_in):
    upload(client, png_bytes(), "holiday photo.png")
    path = avatar()
    assert path.endswith(".jpg") and "holiday" not in path      # random name, not the original
    with Image.open(os.path.join(logged_in, path)) as saved:
        assert saved.format == "JPEG"
        assert max(saved.size) <= 512                             # shrunk


def test_a_file_pretending_to_be_a_picture_is_rejected(client, logged_in):
    upload(client, b"<script>alert(1)</script>", "evil.png")
    assert not avatar()


def test_a_huge_upload_is_refused(client, logged_in):
    response = upload(client, b"x" * (3 * 1024 * 1024), "big.png")   # 3 MB, over the 2 MB limit
    assert response.status_code == 302                                # friendly redirect, not a crash
    assert not avatar()


def test_the_old_picture_is_deleted_when_replaced(client, logged_in):
    upload(client, png_bytes(), "first.png")
    first = os.path.join(logged_in, avatar())
    upload(client, png_bytes(), "second.png")
    assert not os.path.exists(first)
    assert os.path.exists(os.path.join(logged_in, avatar()))
