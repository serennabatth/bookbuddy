"""Account settings - profile, password and privacy."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.services.avatars import delete_avatar_file, save_avatar
from app.services.security import password_problem
from app.services.users import HANDLE_RULES, handle_taken, normalize_handle

bp = Blueprint("settings", __name__)


@bp.route("/settings")
@login_required
def index():
    user = {"name": current_user.name or "", "handle": current_user.handle or ""}
    return render_template("settings.html", user=user)


@bp.route("/edit-profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        # text fields
        new_name = (request.form.get("name") or "").strip()
        new_handle = (request.form.get("handle") or "").strip()
        new_bio = (request.form.get("bio") or "").strip()

        if new_name:
            current_user.name = new_name

        if new_handle:
            handle = normalize_handle(new_handle)
            if handle is None:
                flash(HANDLE_RULES, "error")
                return redirect(url_for("settings.edit_profile"))
            if handle_taken(handle, except_user_id=current_user.id):
                flash(f"{handle} is already taken. Try another handle.", "error")
                return redirect(url_for("settings.edit_profile"))
            current_user.handle = handle

        current_user.bio = new_bio[:200]

        # profile picture
        file = request.files.get("avatar")
        if file and file.filename:
            new_path = save_avatar(file, current_user.id)
            if new_path is None:
                flash("That file isn't a picture we can use. Try a JPG, PNG or WebP image.", "error")
                return redirect(url_for("settings.edit_profile"))
            delete_avatar_file(current_user.avatar)
            current_user.avatar = new_path

        db.session.commit()
        flash("Profile updated.", "ok")
        return redirect(url_for("social.profile_page"))

    # pass the User object so the template can use user.avatar, user.name etc.
    return render_template("edit_profile.html", user=current_user)


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current") or ""
        new = request.form.get("new") or ""
        confirm = request.form.get("confirm") or ""
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("settings.change_password"))
        if password_problem(new):
            flash(password_problem(new), "error")
            return redirect(url_for("settings.change_password"))
        if new != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("settings.change_password"))

        current_user.set_password(new)
        db.session.commit()
        flash("Password updated.", "ok")
        return redirect(url_for("settings.index"))
    return render_template("change_password.html")


@bp.route("/privacy", methods=["GET", "POST"])
@login_required
def privacy_settings():
    if request.method == "POST":
        current_user.is_private = bool(request.form.get("profile_private"))
        db.session.commit()
        flash("Privacy settings saved.", "ok")
        return redirect(url_for("settings.privacy_settings"))
    return render_template("privacy_settings.html", profile_private=current_user.is_private)
