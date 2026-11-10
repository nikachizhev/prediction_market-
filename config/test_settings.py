"""Fast isolated tests; application settings use PostgreSQL."""
from .settings import *

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
# A key from a developer's .env must never make tests call the real model.
LLM_API_KEY = ""
PREMODERATION_ENABLED = False
PREMODERATION_API_KEY = ""
# The old site keeps its tests until it is removed; config/urls.py reads this at import.
LEGACY_SITE_ENABLED = True
