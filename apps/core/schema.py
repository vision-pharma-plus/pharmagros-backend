"""
OpenAPI schema extensions.

drf-spectacular recognises the authentication classes it ships support for by
exact class, not by inheritance, so `StatefulJWTAuthentication` — a subclass of
simplejwt's `JWTAuthentication` — is unknown to it and every view using it
emits a W001 warning and is documented with no security scheme at all.
Registering the extension below restores the bearer-token declaration in the
generated schema.
"""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class StatefulJWTAuthenticationExtension(OpenApiAuthenticationExtension):
    """Documents `StatefulJWTAuthentication` as the standard JWT bearer scheme."""

    target_class = "apps.core.authentication.StatefulJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        # The extra state checks happen server-side after the token validates;
        # from a client's point of view this is an ordinary JWT bearer scheme.
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
