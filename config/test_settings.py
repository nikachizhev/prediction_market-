"""Fast isolated tests; application settings use PostgreSQL."""
from .settings import *

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
# A key from a developer's .env must never make tests call the real model.
LLM_API_KEY = ""
