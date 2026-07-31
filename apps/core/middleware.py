from __future__ import annotations

import uuid

from django.utils import translation

from .context import AuditContext, reset_context, set_context


def client_ip(request) -> str | None:
    """
    Resolve the client IP behind a reverse proxy.

    Only the *first* entry of X-Forwarded-For is meaningful, and only when we
    control the proxy — Nginx is configured to overwrite rather than append
    the header so a client cannot spoof its own address. Falling back to
    REMOTE_ADDR when the header is absent.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class AuditContextMiddleware:
    """Binds actor/IP/user-agent/request-id for the duration of the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get("HTTP_X_REQUEST_ID") or uuid.uuid4().hex
        request.request_id = request_id

        user = getattr(request, "user", None)
        authenticated = bool(user and user.is_authenticated)

        ctx = AuditContext(
            user=user if authenticated else None,
            username=(user.get_username() if authenticated else "anonymous"),
            role=(user.primary_role_name if authenticated else ""),
            ip_address=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
            request_id=request_id,
        )
        token = set_context(ctx)
        try:
            response = self.get_response(request)
        finally:
            reset_context(token)
        response["X-Request-ID"] = request_id
        return response


class UserLanguageMiddleware:
    """
    Applies the authenticated user's stored language preference.

    Ordering note: this must run *after* AuthenticationMiddleware. It defers
    to an explicit Accept-Language override so the frontend's live language
    switch takes effect immediately, without requiring the user to log out
    and back in.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        explicit = request.META.get("HTTP_X_LANGUAGE")
        language = None

        if explicit in {"fr", "en"}:
            language = explicit
        elif user is not None and user.is_authenticated:
            language = getattr(user, "language", None)

        if language:
            translation.activate(language)
            request.LANGUAGE_CODE = language
        try:
            return self.get_response(request)
        finally:
            translation.deactivate()
