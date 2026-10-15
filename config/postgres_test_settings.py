"""Isolated PostgreSQL tests, including real row-lock concurrency checks."""
import os
from uuid import uuid4
from .settings import *

DATABASES['default']['TEST'] = {'NAME': os.environ.get('SAFETY_TEST_DATABASE', 'test_prediction_market_'+uuid4().hex)}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
