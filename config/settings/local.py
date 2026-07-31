"""
Local development settings — SQLite, no Redis, no external services.

Lets the whole stack run from a clean checkout with nothing installed beyond
Python packages. Swap to PostgreSQL later by setting DATABASE_URL and using
`config.settings.dev` instead.
"""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "local-dev-key-not-for-production-000000")
os.environ.setdefault("DATABASE_URL", "sqlite:///local.sqlite3")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from .base import *  # noqa: E402,F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "local.sqlite3",  # noqa: F405
    }
}

# Local-memory cache: no Redis needed to run the app.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Celery runs inline, so scheduled tasks can be triggered by hand without a
# worker or broker process.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Emails print to the console instead of requiring an SMTP host.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# No TLS terminator in front of runserver.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

# The Next.js dev server runs on :3000.
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
CORS_ALLOW_ALL_ORIGINS = True

FRONTEND_URL = "http://localhost:3000"

# Forgiving lockout so a forgotten password does not block a dev session.
AXES_FAILURE_LIMIT = 50

LOGGING["handlers"]["console"]["formatter"] = "plain"  # noqa: F405
LOGGING["root"]["level"] = "INFO"  # noqa: F405
