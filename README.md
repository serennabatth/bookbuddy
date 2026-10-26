# BookBuddy

BookBuddy is a full-stack web application designed to help readers discover, review, and track books while connecting with other users.

This project was built alongside my Computing & IT with Business degree as a portfolio project demonstrating backend development, database handling, authentication systems, and UI design.

---

## Features

- User authentication (sign up, login, logout)
- Profile editing and settings
- Book browsing and search
- User reviews and ratings
- Favourite books system
- Password reset functionality
- Responsive mobile-friendly design

---

## Tech Stack

**Backend**
- Python
- Flask

**Frontend**
- HTML
- CSS
- Jinja Templates

**Other Tools**
- Git & GitHub
- VS Code

---

## Running the Project Locally

Developed with Python 3.13. Commands are for Windows PowerShell, with macOS/Linux versions in the comments.

```bash
git clone https://github.com/serennabatth/bookbuddy.git
cd bookbuddy
python -m venv venv
venv\Scripts\activate              # macOS/Linux: source venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env              # macOS/Linux: cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the printed value after `SECRET_KEY=` in `.env`, then:

```bash
flask db upgrade         # create the database tables
flask seed               # add starter books (needs internet)
flask run --debug        # open http://127.0.0.1:5000
```

`.flaskenv` tells the `flask` command to load the app from `wsgi.py`.

## Project Structure

```
app/
  __init__.py      create_app() - builds the app
  config.py        settings for development, testing and production
  extensions.py    database, migrations, login and CSRF setup
  models.py        database tables
  cli.py           `flask seed` command
  blueprints/      routes, grouped by area (auth, books, library, reviews, social, settings)
  services/        logic used by the routes (Open Library, catalogue, security, users, avatars)
templates/         Jinja templates
static/            CSS, images and uploaded avatars
migrations/        database migrations (Alembic)
tests/             pytest tests
wsgi.py            entry point
```

## Running the Tests

```bash
pytest
```

The tests use a temporary in-memory database, so they never change your local data.
