"""
Test settings.

Currently targets SQLite so the suite runs anywhere without provisioning a
database. That is a deliberate, temporary trade — several correctness
properties this system relies on cannot be exercised on SQLite:

  * the audit-immutability triggers (PL/pgSQL, skipped on non-PostgreSQL)
  * genuine SELECT ... FOR UPDATE row locking under concurrency
  * JSONB indexing on the audit payload

Those are covered by the CI pipeline, which runs against PostgreSQL 16. To
switch locally, set DATABASE_URL to a Postgres URL and delete the DATABASES
override below — nothing else needs to change.
"""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-not-used-in-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///test.sqlite3")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from .base import *  # noqa: E402,F401,F403

DEBUG = False
ALLOWED_HOSTS = ["*"]

# --- SQLite for local runs; remove this block to use PostgreSQL -------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
    }
}

# Fast hashing: Argon2 is deliberately slow, which would dominate test runtime
# for no benefit. The production hasher is asserted separately.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

# Lockout interferes with tests that intentionally submit bad credentials.
AXES_ENABLED = False

# Throttling would make repeated auth calls in the suite fail spuriously.
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {
        "auth": None,
        "password_reset": None,
        "reports": None,
        "default": None,
    },
}

LOGGING["root"]["level"] = "ERROR"  # noqa: F405
LOGGING["handlers"]["console"]["formatter"] = "plain"  # noqa: F405
