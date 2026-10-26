"""Sign up, log in, log out and password resets."""
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import login_user, logout_user

from app.extensions import db
from app.models import User
from app.services.security import (
    generate_reset_token, password_problem, rate_limited, safe_next_url, verify_reset_token,
)
from app.services.users import HANDLE_RULES, handle_taken, normalize_handle

bp = Blueprint("auth", __name__)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "")
        name = (request.form.get("name") or "").strip() or "New Reader"
        handle_raw = (request.form.get("handle") or "").strip()
        handle = normalize_handle(handle_raw) if handle_raw else None

        if not email or not password:
            flash("Email and password are required.", "error")
            return redirect(url_for("auth.signup"))
        if password_problem(password):
            flash(password_problem(password), "error")
            return redirect(url_for("auth.signup"))
        if handle_raw and handle is None:
            flash(HANDLE_RULES, "error")
            return redirect(url_for("auth.signup"))
        if handle and handle_taken(handle):
            flash(f"{handle} is already taken. Try another handle.", "error")
            return redirect(url_for("auth.signup"))

        if User.query.filter_by(email=email).first():
            flash("This email is already registered. Try logging in.", "error")
            return redirect(url_for("auth.login"))

        user = User(email=email, name=name, handle=handle)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for("books.home"))

    return render_template("sign_up.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if rate_limited("login", limit=5, window=60):
            flash("Too many sign-in attempts. Please wait a minute and try again.", "error")
            return render_template("sign_in.html"), 429

        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "")
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("auth.login", next=request.args.get("next")))

        # only stay logged in for 30 days if "Remember me" was ticked
        login_user(user, remember=bool(request.form.get("remember")))
        return redirect(safe_next_url(request.args.get("next")) or url_for("books.home"))
    return render_template("sign_in.html")


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        if rate_limited("forgot-password", limit=3, window=600):
            flash("Too many reset requests. Please try again in a few minutes.", "error")
            return render_template("forgot_password.html"), 429

        email = (request.form.get("email") or "").strip().lower()

        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_reset_token(user)
            reset_link = url_for("auth.reset_password", token=token, _external=True)

            
            # TODO: send by email - for now the link is just printed to the log
            current_app.logger.warning("PASSWORD RESET LINK for %s: %s", user.email, reset_link)

        flash("If that email exists, you’ll receive a password reset link shortly.", "ok")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = verify_reset_token(token, max_age_seconds=60 * 60)
    if user is None:
        flash("That reset link is invalid, expired or already used. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_pw = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if password_problem(new_pw):
            flash(password_problem(new_pw), "error")
            return redirect(url_for("auth.reset_password", token=token))
        if new_pw != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("auth.reset_password", token=token))

        user.set_password(new_pw)
        db.session.commit()
        flash("Password updated. You can sign in now.", "ok")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)
