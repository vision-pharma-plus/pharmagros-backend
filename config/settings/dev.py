"""Development overrides. Every relaxation here is deliberate and listed."""

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = True
ALLOWED_HOSTS = ["*"]

# No TLS terminator in front of runserver.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

CORS_ALLOW_ALL_ORIGINS = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

LOGGING["handlers"]["console"]["formatter"] = "plain"  # noqa: F405
LOGGING["root"]["level"] = "DEBUG"  # noqa: F405

# Lockout still active in dev, but forgiving enough not to block developers.
AXES_FAILURE_LIMIT = 20

# Run Celery tasks inline so a dev machine needs no worker process.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)
CELERY_TASK_EAGER_PROPAGATES = True
