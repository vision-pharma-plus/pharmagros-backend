"""
Settings for offline validation: `check`, `makemigrations`, schema generation.

Uses SQLite and a local-memory cache so the full model layer, URLconf and
serializers can be validated in CI without provisioning PostgreSQL or Redis.

NOT usable for running the application: several features depend on PostgreSQL
specifically (the audit-immutability triggers, SELECT ... FOR UPDATE row
locking in FIFO allocation, JSONB indexing). Those are exercised by the
integration test suite against a real Postgres instance.
"""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "check-only-not-a-real-secret-key-000000")
os.environ.setdefault("DATABASE_URL", "sqlite:///check.sqlite3")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from .base import *  # noqa: E402,F401,F403

DEBUG = False
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "check.sqlite3",  # noqa: F405
    }
}

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True

LOGGING["handlers"]["console"]["formatter"] = "plain"  # noqa: F405
