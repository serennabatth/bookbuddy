"""Entry point for running the app.

    flask run --debug     (FLASK_APP=wsgi is set in .flaskenv)

In production a server like Gunicorn loads `app` from here: gunicorn wsgi:app
"""
from app import create_app

app = create_app()
