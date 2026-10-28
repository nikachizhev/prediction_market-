import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "market",
    "sources",
    "abmock",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "market.middleware.DailyRewardMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "market.context_processors.current_trader",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "prediction_market"),
        "USER": os.environ.get("POSTGRES_USER", "prediction_market"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "local-development-password"),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "market:login"
LOGIN_REDIRECT_URL = "market:welcome"
LOGOUT_REDIRECT_URL = "market:markets"
PASSWORD_RESET_TIMEOUT = 3600

LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Economy (spec E5). The only place these numbers live; screens read them from the API.
START_BALANCE = 1000            # starter pack, once per Telegram ID
DAILY_BONUS = 20                # first open of the day (Moscow date)
INSURANCE_AMOUNT = 200          # once a week if balance < threshold and no open positions
INSURANCE_THRESHOLD = 50
INSURANCE_PERIOD_DAYS = 7
TRADE_LIMIT_SHARE = "0.20"      # one trade: at most 20% of balance...
MAX_TRADE = 200                 # ...and at most this
MARKET_CAP = 500                # invested in one market at most
MARKET_CREATION_COST = 100      # subsidy for the market maker, covers b*ln2
LMSR_DEFAULT_B = 144            # 144 * ln 2 ~= 99.8 <= MARKET_CREATION_COST
TRADE_FEE = "0.02"              # of every trade; held in the market until it is settled
CREATOR_FEE_SHARE = "0.5"       # the creator's part of the fee; the rest burns
DISPUTE_COST = 20               # refunded if the dispute is upheld
DISPUTE_WINDOW_HOURS = 24
RESOLUTION_TIMEOUT_HOURS = 72   # creator silent this long after resolves_at -> market is cancelled
MARKETS_PER_DAY = 3
MARKET_MIN_HOURS = 1
MARKET_MAX_DAYS = 365
COMPLAINTS_TO_HIDE = 3
REFERRAL_REWARD = 300           # to both the friend and the inviter...
REFERRAL_PREDICTIONS = 5        # ...once the friend has bought in this many different markets
REFERRAL_MONTHLY_CAP = 10       # rewards per inviter per calendar month (Moscow)
NOTIFY_DAILY_CAP = 5            # bot notifications per user per Moscow day (spec E6.3)
NOTIFY_QUIET_HOURS = (23, 9)    # Moscow time: queued notifications wait until the end
NOTIFY_MOVE_POINTS = 10         # a tracked market moved this many percentage points in 24 h
NOTIFY_CLOSING_HOURS = 24       # «до конца приёма прогнозов 24 часа»
BOT_MARKETS_PAGE = 10           # «Мои рынки» in the bot: buttons per message
RATING_ACCURACY_MIN_RESOLVED = 20  # accuracy rating counts traders with at least this many resolved predictions
MAX_BET = MAX_TRADE             # legacy name used by the old server-rendered pages
AB_BASE_RATE = 0.25
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MODERATION_BOT_TOKEN = os.environ.get("MODERATION_BOT_TOKEN", "")
MODERATION_ADMIN_IDS = tuple(x.strip() for x in os.environ.get("MODERATION_ADMIN_IDS", "").split(",") if x.strip())
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "").lstrip("@")
TELEGRAM_APP_NAME = os.environ.get("TELEGRAM_APP_NAME", "app")  # short name of the Mini App in BotFather
TELEGRAM_INIT_DATA_MAX_AGE = 24 * 3600
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
# Production runs behind a TLS-terminating reverse proxy (Caddy, see deploy/).
CSRF_TRUSTED_ORIGINS = [SITE_URL] if SITE_URL.startswith("https://") else []
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "noreply@localhost")

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
MINIAPP_DIST = Path(os.environ.get("MINIAPP_DIST", BASE_DIR / "miniapp" / "dist"))
