"""
Production settings.

Deliberately thin: base.py is already the hardened configuration. This module
only adds observability and asserts that required secrets were actually
supplied, so a misconfigured deploy fails at boot rather than at 3am.
"""

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

# Fail fast on configuration mistakes that would otherwise silently degrade
# security in production.
if not ALLOWED_HOSTS:  # noqa: F405
    raise RuntimeError("DJANGO_ALLOWED_HOSTS must be set in production.")
if SIMPLE_JWT["SIGNING_KEY"] == SECRET_KEY:  # noqa: F405
    # Sharing the Django SECRET_KEY with JWT signing means a leak of one
    # compromises both. Acceptable for dev, not for production.
    import warnings

    warnings.warn(
        "JWT_SIGNING_KEY is not set separately from DJANGO_SECRET_KEY. "
        "Set a distinct key so signing material is compartmentalised.",
        RuntimeWarning,
        stacklevel=1,
    )

_sentry_dsn = env("SENTRY_DSN", default="")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1),
        # Pharmaceutical + financial data: never ship request bodies or PII
        # to a third-party error tracker.
        send_default_pii=False,
        environment=env("ENVIRONMENT", default="production"),
    )
