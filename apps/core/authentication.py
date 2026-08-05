"""
JWT authentication that re-checks account and session state per request.

`rest_framework_simplejwt.JWTAuthentication` validates a token's signature and
expiry and then trusts its claims. That is sound for a stateless design, but
this system offers "sign out everywhere", administrative suspension and
session revocation — all of which must take effect *now*, not whenever the
access token happens to expire. With the stock class, revoking a session left
the holder working for the remainder of the access-token lifetime.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class StatefulJWTAuthentication(JWTAuthentication):
    """Validates the token, then confirms the account and session still stand."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        # `is_active` is already checked by the parent, but suspension is this
        # project's own reversible state and the parent knows nothing of it.
        if getattr(user, "is_suspended", False):
            raise AuthenticationFailed(
                _("This account is suspended. Please contact your administrator."),
                code="account_suspended",
            )

        self._check_session(user, validated_token)
        return user

    def _check_session(self, user, validated_token) -> None:
        """
        Reject an access token whose originating session has been revoked.

        Access tokens carry the refresh token's jti in their own claims only
        when issued through the login/refresh flow, so a token minted directly
        (as tests and management commands do) has no session to check and is
        accepted on the strength of its signature alone.
        """
        from apps.accounts.models import UserSession

        jti = validated_token.get("session_jti")
        if not jti:
            return

        session = UserSession.objects.filter(jti=jti).only("revoked_at", "expires_at").first()
        if session is None:
            # The session row was pruned (expired sessions are cleaned up by a
            # periodic task) but the access token is still within its own
            # lifetime. Treat as revoked: a session we cannot vouch for is not
            # one we should honour.
            raise AuthenticationFailed(
                _("This session is no longer valid. Please sign in again."),
                code="session_not_found",
            )
        if not session.is_active:
            raise AuthenticationFailed(
                _("This session has been revoked. Please sign in again."),
                code="session_revoked",
            )
