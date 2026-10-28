"""Explicitly isolated local demo. Never use for production."""
from .settings import *

OBSERVABILITY_DEMO = True
TELEGRAM_BOT_TOKEN = ''
TELEGRAM_BOT_USERNAME = ''
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEBUG = False
