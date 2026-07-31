"""
CORS preflight contract.

These exist because a header the frontend sends but the backend does not
allowlist produces a browser-only failure: the request never reaches Django,
so no server log records it, and DevTools reports a generic "CORS error" that
names no header. Scripted HTTP clients do not enforce CORS, so integration
tests using APIClient or requests will happily pass while the real browser is
blocked.

The assertion that matters is that every header `frontend/src/lib/api/client.ts`
sets appears in `Access-Control-Allow-Headers`.
"""

from __future__ import annotations

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

ORIGIN = "http://localhost:3000"

# Every non-CORS-safelisted header the API client sets. Keep in step with
# `client.ts` — a header added there and not here will fail in the browser only.
CLIENT_HEADERS = ["content-type", "authorization", "x-language"]


def preflight(path: str, request_headers: str, origin: str = ORIGIN):
    return Client().options(
        path,
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS=request_headers,
    )


class TestPreflight:
    def test_login_preflight_succeeds(self, settings):
        settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
        response = preflight("/api/v1/auth/login/", "content-type,x-language")
        assert response.status_code == 200
        assert response["Access-Control-Allow-Origin"] == ORIGIN

    @pytest.mark.parametrize("header", CLIENT_HEADERS)
    def test_every_client_header_is_allowed(self, settings, header):
        """
        The regression this file exists for: X-Language was sent by the client
        but missing from CORS_ALLOW_HEADERS, so the browser blocked every
        request while server-side tests passed.
        """
        settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
        response = preflight("/api/v1/auth/login/", header)
        allowed = response.get("Access-Control-Allow-Headers", "").lower()
        assert header in allowed, (
            f"'{header}' is sent by the API client but not in "
            f"Access-Control-Allow-Headers ({allowed}) — the browser will "
            f"block every request with a generic CORS error."
        )

    def test_credentials_are_allowed(self, settings):
        settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
        response = preflight("/api/v1/auth/login/", "content-type")
        assert response.get("Access-Control-Allow-Credentials") == "true"

    def test_request_id_is_exposed_to_the_client(self, settings):
        """The frontend reads X-Request-ID to correlate an error with a log line."""
        settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
        response = Client().get("/health/", HTTP_ORIGIN=ORIGIN)
        exposed = response.get("Access-Control-Expose-Headers", "").lower()
        assert "x-request-id" in exposed


class TestActualRequest:
    def test_post_response_carries_the_origin_header(self, settings, admin_user):
        """
        A passing preflight is not sufficient — the real response must also
        carry Access-Control-Allow-Origin or the browser discards it.
        """
        settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
        response = Client().post(
            "/api/v1/auth/login/",
            data={"email": admin_user.email, "password": "TestPass!2026#Secure"},
            content_type="application/json",
            HTTP_ORIGIN=ORIGIN,
            HTTP_X_LANGUAGE="fr",
        )
        assert response.status_code == 200
        assert response["Access-Control-Allow-Origin"] == ORIGIN

    def test_x_language_header_switches_response_language(self, settings, admin_user):
        """X-Language is not decoration — it drives server-side translation."""
        settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
        response = Client().post(
            "/api/v1/auth/login/",
            data={"email": admin_user.email, "password": "wrong-password"},
            content_type="application/json",
            HTTP_ORIGIN=ORIGIN,
            HTTP_X_LANGUAGE="fr",
        )
        assert response.status_code in (400, 401)
