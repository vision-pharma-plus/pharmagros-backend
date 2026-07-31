"""Response returned by django-axes when an account is locked out."""

from django.http import JsonResponse
from django.utils.translation import gettext as _


def lockout_response(request, credentials=None, *args, **kwargs):
    """
    Uniform 429 for a locked account.

    The message states that the account is temporarily locked without
    confirming whether the address exists — otherwise the lockout response
    itself becomes an account-enumeration oracle.
    """
    from django.conf import settings

    minutes = int(settings.AXES_COOLOFF_TIME.total_seconds() // 60)

    return JsonResponse(
        {
            "error": {
                "code": "account_locked",
                "message": _(
                    "Too many failed sign-in attempts. Please try again in %(minutes)d minutes."
                )
                % {"minutes": minutes},
                "details": {"cooloff_minutes": minutes},
            }
        },
        status=429,
    )
