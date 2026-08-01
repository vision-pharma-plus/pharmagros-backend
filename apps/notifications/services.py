"""
Notification dispatch.

All alert creation goes through `notify`, which applies deduplication before
writing. Without that, the daily expiry scan would re-alert about the same
batch every morning until someone acts — and an inbox that cries wolf gets
ignored, which is worse than no alert at all.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone, translation

from .models import Notification, NotificationSeverity

logger = logging.getLogger(__name__)

# How long the same dedupe_key is suppressed for, per severity. Critical
# alerts repeat sooner because they demand action.
DEDUPE_WINDOWS = {
    NotificationSeverity.INFO: timezone.timedelta(days=7),
    NotificationSeverity.WARNING: timezone.timedelta(days=3),
    NotificationSeverity.CRITICAL: timezone.timedelta(hours=12),
}


def _is_duplicate(recipient_id, dedupe_key: str, severity: str) -> bool:
    if not dedupe_key:
        return False
    window = DEDUPE_WINDOWS.get(severity, timezone.timedelta(days=3))
    return Notification.objects.filter(
        recipient_id=recipient_id,
        dedupe_key=dedupe_key,
        created_at__gte=timezone.now() - window,
    ).exists()


@transaction.atomic
def notify(
    *,
    recipients,
    code: str,
    title: str,
    body: str = "",
    severity: str = NotificationSeverity.INFO,
    link: str = "",
    entity_type: str = "",
    entity_id: str = "",
    dedupe_key: str = "",
    send_email: bool = False,
) -> list[Notification]:
    """Create notifications for a set of users, skipping recent duplicates."""
    created: list[Notification] = []

    for recipient in recipients:
        if _is_duplicate(recipient.pk, dedupe_key, severity):
            continue

        notification = Notification.objects.create(
            recipient=recipient,
            code=code,
            severity=severity,
            title=title,
            body=body,
            link=link,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else "",
            dedupe_key=dedupe_key,
        )
        created.append(notification)

        if send_email and recipient.email:
            # Queued on commit so a rolled-back transaction never sends an
            # email about something that did not happen.
            transaction.on_commit(
                lambda n=notification, r=recipient: _send_email(n, r)
            )

    return created


def _send_email(notification: Notification, recipient) -> None:
    """Send a notification by email in the recipient's own language."""
    try:
        with translation.override(recipient.language or "fr"):
            subject = f"[{settings.COMPANY['NAME']}] {notification.title}"
            context = {
                "notification": notification,
                "recipient": recipient,
                "company": settings.COMPANY,
            }
            text_body = render_to_string("emails/notification.txt", context)
            html_body = render_to_string("emails/notification.html", context)

            send_mail(
                subject=subject,
                message=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient.email],
                html_message=html_body,
                fail_silently=False,
            )
        notification.emailed_at = timezone.now()
        notification.save(update_fields=["emailed_at"])
    except Exception:
        # An email failure must never break the business transaction that
        # triggered it — the in-app notification is already persisted.
        logger.exception(
            "notification_email_failed",
            extra={"notification_id": notification.pk, "recipient": recipient.email},
        )


def notify_permission_holders(
    *, permission_code: str, code: str, title: str, body: str = "", **kwargs
) -> list[Notification]:
    """
    Notify every active user holding a given permission.

    Targeting by permission rather than by role means an alert reaches whoever
    can actually act on it, even after roles are reorganised.
    """
    from apps.accounts.models import User

    recipients = [
        user
        for user in User.objects.filter(is_active=True, is_suspended=False).prefetch_related(
            "roles__permissions"
        )
        if user.has_perm_code(permission_code)
    ]
    return notify(recipients=recipients, code=code, title=title, body=body, **kwargs)


def notify_administrators(
    *, code: str, title: str, body: str = "",
    severity: str = NotificationSeverity.CRITICAL, **kwargs
) -> list[Notification]:
    """Notify system administrators. Used for integrity and security events."""
    from apps.accounts.models import User

    recipients = User.objects.filter(
        is_active=True, is_suspended=False,
    ).filter(roles__code="system-administrator").distinct()

    return notify(
        recipients=list(recipients),
        code=code,
        title=title,
        body=body,
        severity=severity,
        send_email=True,
        **kwargs,
    )


def mark_all_read(user) -> int:
    """Mark every unread notification for a user as read."""
    return user.notifications.filter(read_at__isnull=True).update(
        read_at=timezone.now(), updated_at=timezone.now()
    )


def unread_count(user) -> int:
    return user.notifications.filter(read_at__isnull=True, dismissed_at__isnull=True).count()
