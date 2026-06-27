"""Django settings for the OmSree Sync Agent control panel.

Single-machine, localhost-only admin app. Reuses the `agent/` package (one level up)
as the sync engine, and stores everything in a local SQLite DB.
"""

from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent          # .../controlpanel
REPO_ROOT = BASE_DIR.parent                                 # repo root (holds agent/)

# Make the existing `agent` package importable.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Where the SQLite DB, secret key, and logs live (kept out of source control).
STATE_DIR = Path(__import__("os").environ.get("AGENT_STATE_DIR", BASE_DIR / "instance"))
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _load_secret_key() -> str:
    key_file = STATE_DIR / "django_secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    from django.core.management.utils import get_random_secret_key
    key = get_random_secret_key()
    key_file.write_text(key, encoding="utf-8")
    return key


SECRET_KEY = _load_secret_key()

# Localhost-only app. Never expose on the LAN.
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:8787", "http://localhost:8787"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "syncadmin",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "controlpanel.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "controlpanel.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": STATE_DIR / "agent.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = STATE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Send non-staff users who hit a protected view to the login page.
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# Start the in-process APScheduler cron when serving (set to "0" to disable,
# e.g. for tests). The AppConfig also skips it during migrate/makemigrations.
SCHEDULER_AUTOSTART = __import__("os").environ.get("SCHEDULER_AUTOSTART", "1") == "1"

# Rolling agent log shared with the engine.
SYNC_LOG_FILE = STATE_DIR / "sync.log"
