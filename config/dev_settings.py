"""Local development settings using SQLite for easy testing without Docker."""
from .settings import *
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = True
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "dev_db.sqlite3",
    }
}
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

