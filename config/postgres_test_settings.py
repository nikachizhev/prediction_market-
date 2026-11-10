"""Isolated PostgreSQL tests, including real row-lock concurrency checks."""
import os
from uuid import uuid4
from .settings import *

DATABASES['default']['TEST'] = {'NAME': os.environ.get('SAFETY_TEST_DATABASE', 'test_prediction_market_'+uuid4().hex)}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
# A key from a developer's .env must never make tests call the real model.
LLM_API_KEY = ""
PREMODERATION_ENABLED = False
PREMODERATION_API_KEY = ""
# The old site keeps its tests until it is removed; config/urls.py reads this at import.
LEGACY_SITE_ENABLED = True
