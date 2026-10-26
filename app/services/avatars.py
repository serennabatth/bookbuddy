"""Profile picture uploads."""
import os
import secrets

from flask import current_app
from PIL import Image, UnidentifiedImageError

AVATAR_FOLDER = os.path.join("uploads", "avatars")   # inside static/
AVATAR_MAX_SIZE = (512, 512)

# Stops "decompression bombs" - small files that expand into huge images
Image.MAX_IMAGE_PIXELS = 25_000_000


def save_avatar(file, user_id: int) -> str | None:
    """Validate and save an uploaded picture. Returns the path inside static/, or None.

    - Has to actually open as an image (the file extension doesn't prove anything)
    - Resized to max 512x512 and saved as a new JPEG, which also strips
      metadata like GPS location from phone photos
    - Random filename so it can't be guessed or overwritten
    """
    try:
        with Image.open(file.stream) as img:
            img.load()
            img = img.convert("RGB")
            img.thumbnail(AVATAR_MAX_SIZE)
            filename = f"user_{user_id}_{secrets.token_hex(8)}.jpg"
            folder = os.path.join(current_app.static_folder, AVATAR_FOLDER)
            os.makedirs(folder, exist_ok=True)
            img.save(os.path.join(folder, filename), "JPEG", quality=85)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    return f"{AVATAR_FOLDER.replace(os.sep, '/')}/{filename}"


def delete_avatar_file(path: str | None) -> None:
    """Delete an old profile picture (only if it's in our avatars folder)."""
    if not path or not path.startswith("uploads/avatars/"):
        return
    full = os.path.join(current_app.static_folder, *path.split("/"))
    try:
        os.remove(full)
    except FileNotFoundError:
        pass
