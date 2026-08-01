"""
HTTP transport for the OBR EBMS service.

Three concerns live here and nowhere else: obtaining and reusing the bearer
token, issuing the request, and recording what crossed the wire.

That last point is not incidental. When the OBR and our own records disagree
about what was filed, the stored request and response *are* the evidence, and
they must survive independently of whether the submission eventually
succeeded. Every attempt is therefore logged before it is made, not after it
returns.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.exceptions import BusinessRuleViolation

logger = logging.getLogger(__name__)

TOKEN_CACHE_KEY = "obr:auth_token"
# Renew slightly before the token actually expires, so a request never starts
# with a token that lapses mid-flight.
TOKEN_RENEWAL_MARGIN = timedelta(seconds=60)


class OBRError(Exception):
    """A call to the OBR did not succeed."""

    def __init__(self, message: str, *, status_code: int | None = None, payload=None, retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload
        # Distinguishes "the link is down, try later" from "this document is
        # malformed and will never be accepted". Only the former is requeued.
        self.retryable = retryable


@dataclass
class OBRResponse:
    status_code: int
    body: dict | list | str | None

    @property
    def result(self) -> dict:
        """The OBR nests successful payloads under a `result` key."""
        if isinstance(self.body, dict):
            value = self.body.get("result")
            if isinstance(value, dict):
                return value
        return {}


def is_enabled() -> bool:
    return bool(settings.OBR["ENABLED"])


def _config_error(message: str) -> BusinessRuleViolation:
    return BusinessRuleViolation(message, code="obr_not_configured")


def _endpoint(name: str) -> str:
    base = settings.OBR["BASE_URL"].rstrip("/")
    path = settings.OBR["PATHS"][name].lstrip("/")
    return f"{base}/{path}"


def _timeouts() -> tuple[float, float]:
    return settings.OBR["CONNECT_TIMEOUT"], settings.OBR["READ_TIMEOUT"]


def _token_expiry(token: str):
    """
    Read the expiry claim from a bearer token.

    The claims are decoded without verifying the signature, which is safe
    only because the single value taken is the expiry and it is used solely
    to decide when to ask for a new token. Nothing here is trusted for
    authorisation; the OBR remains the authority on whether a token is valid.
    A token we cannot parse is simply treated as short-lived.
    """
    try:
        claims_segment = token.split(".")[1]
        padding = "=" * (-len(claims_segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(claims_segment + padding))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        return None

    expiry = claims.get("exp")
    if not isinstance(expiry, (int, float)):
        return None
    # An `exp` claim is seconds since the epoch in UTC by definition.
    return datetime.fromtimestamp(expiry, tz=UTC)


def authenticate(*, force: bool = False) -> str:
    """
    Return a bearer token, reusing the cached one until it nears expiry.

    The token is cached rather than re-fetched per call because a sweep may
    declare dozens of documents in one pass, and re-authenticating for each
    would multiply the load on a service that is not fast.
    """
    if not force:
        cached = cache.get(TOKEN_CACHE_KEY)
        if cached:
            return cached

    username = settings.OBR["USERNAME"]
    password = settings.OBR["PASSWORD"]
    if not username or not password:
        raise _config_error(_("OBR credentials are not configured."))

    connect_timeout, read_timeout = _timeouts()
    try:
        response = requests.post(
            _endpoint("login"),
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=(connect_timeout, read_timeout),
        )
    except requests.RequestException as exc:
        raise OBRError(f"Could not reach the OBR authentication service: {exc}") from exc

    if response.status_code != 200:
        raise OBRError(
            f"OBR authentication was refused (HTTP {response.status_code}).",
            status_code=response.status_code,
            # Bad credentials will not fix themselves on retry.
            retryable=response.status_code >= 500,
        )

    try:
        token = response.json().get("result", {}).get("token")
    except ValueError as exc:
        raise OBRError("The OBR authentication response was not valid JSON.") from exc

    if not token:
        raise OBRError("The OBR authentication response contained no token.", retryable=False)

    expiry = _token_expiry(token)
    if expiry:
        lifetime = int((expiry - timezone.now() - TOKEN_RENEWAL_MARGIN).total_seconds())
    else:
        lifetime = 0
    # A token whose lifetime cannot be established is still cached briefly:
    # long enough to serve one sweep, short enough not to go stale.
    cache.set(TOKEN_CACHE_KEY, token, timeout=max(lifetime, 300))
    return token


def call(endpoint_name: str, body: dict, *, invoice=None, operation: str = "") -> OBRResponse:
    """
    Send one request to the OBR, recording it against the invoice.

    The log row is written first and updated in place with the outcome, so a
    request that never returns still leaves a trace of having been attempted.
    """
    from ..models import FiscalRequestLog

    if not is_enabled():
        raise _config_error(_("OBR declaration is disabled in this environment."))

    url = _endpoint(endpoint_name)
    log = FiscalRequestLog.objects.create(
        invoice=invoice,
        operation=operation or endpoint_name,
        endpoint=url,
        request_body=body,
    )

    connect_timeout, read_timeout = _timeouts()
    try:
        token = authenticate()
        response = requests.post(
            url,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            timeout=(connect_timeout, read_timeout),
        )
    except OBRError as exc:
        log.mark_failed(str(exc))
        raise
    except requests.RequestException as exc:
        log.mark_failed(f"Transport error: {exc}")
        raise OBRError(f"Could not reach the OBR: {exc}") from exc

    try:
        parsed = response.json()
    except ValueError:
        parsed = response.text

    result = OBRResponse(status_code=response.status_code, body=parsed)

    if response.status_code in (200, 201):
        log.mark_succeeded(response.status_code, parsed)
        return result

    # A token rejected mid-sweep usually means it expired early; drop it so
    # the next attempt authenticates afresh rather than replaying a dead one.
    if response.status_code in (401, 403):
        cache.delete(TOKEN_CACHE_KEY)

    log.mark_failed(_describe_failure(parsed), status_code=response.status_code, body=parsed)
    raise OBRError(
        _describe_failure(parsed),
        status_code=response.status_code,
        payload=parsed,
        # 4xx other than auth means the document itself is unacceptable.
        retryable=response.status_code >= 500 or response.status_code in (401, 403, 408, 429),
    )


def _describe_failure(payload) -> str:
    """Extract the most useful message the OBR offered."""
    if isinstance(payload, dict):
        for key in ("msg", "message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(payload, ensure_ascii=False)[:500]
    if isinstance(payload, list) and payload:
        return str(payload[0])[:500]
    if isinstance(payload, str) and payload.strip():
        return payload[:500]
    return "The OBR rejected the request without explanation."
