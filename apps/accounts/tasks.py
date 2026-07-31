import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


@shared_task(name="apps.accounts.tasks.send_password_reset_email")
def send_password_reset_email(user_id: str, uid: str, token: str) -> bool:
    """
    Email a password reset link in the user's own language.

    Runs off the request path so reset-request latency does not depend on the
    mail server — and so a slow SMTP host cannot be used to probe which
    addresses exist by timing.
    """
    from .models import User

    user = User.objects.filter(pk=user_id).first()
    if not user:
        return False

    reset_url = f"{settings.FRONTEND_URL}/auth/reset-password?uid={uid}&token={token}"

    try:
        with translation.override(user.language or "fr"):
            context = {
                "user": user,
                "reset_url": reset_url,
                "company": settings.COMPANY,
                "validity_hours": 24,
            }
            send_mail(
                subject=_("%(company)s — Password reset")
                % {"company": settings.COMPANY["NAME"]},
                message=render_to_string("emails/password_reset.txt", context),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=render_to_string("emails/password_reset.html", context),
                fail_silently=False,
            )
        return True
    except Exception:
        logger.exception("password_reset_email_failed", extra={"user_id": user_id})
        return False


@shared_task(name="apps.accounts.tasks.purge_expired_sessions")
def purge_expired_sessions() -> int:
    """Remove session records that expired more than 90 days ago."""
    from django.utils import timezone

    from .models import UserSession

    cutoff = timezone.now() - timezone.timedelta(days=90)
    deleted, _detail = UserSession.objects.filter(expires_at__lt=cutoff).delete()
    return deleted
