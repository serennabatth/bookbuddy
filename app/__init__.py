"""BookBuddy - find, track and review books."""
import os

from flask import Flask, flash, jsonify, redirect, request, url_for
from flask_wtf.csrf import CSRFError

from app.config import BASE_DIR, get_config
from app.extensions import csrf, db, login_manager, migrate


def create_app(config_name: str | None = None) -> Flask:
    """App factory - builds and returns a configured app.

    config_name is "development", "testing" or "production". If it's not
    given, BOOKBUDDY_ENV decides.
    """
    # templates/ and static/ are in the project root rather than inside app/
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
    )

    config_class = get_config(config_name)
    app.config.from_object(config_class)
    config_class.init_app(app)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)

    # imported here so models are loaded before migrations/blueprints need them
    from app import models  # noqa: F401
    from app.blueprints import auth, books, library, reviews, settings, social
    from app.cli import seed_command
    from app.services.catalogue import PLACEHOLDER_COVER

    for module in (auth, books, library, reviews, settings, social):
        app.register_blueprint(module.bp)
    app.cli.add_command(seed_command)

    register_error_handlers(app)

    @app.context_processor
    def inject_globals():
        return {"PLACEHOLDER_COVER": PLACEHOLDER_COVER}

    return app


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError):
        """Show a helpful message instead of a plain 400 page."""
        message = "Your session expired or the form was out of date. Please try again."
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "error")
        if request.endpoint == "auth.logout":
            return redirect(url_for("books.home"))
        return redirect(request.path)

    @app.errorhandler(413)
    def file_too_large(error):
        flash("That picture is too big. Please choose one under 2 MB.", "error")
        return redirect(url_for("settings.edit_profile"))
