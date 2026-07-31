"""In-app notifications and alerts."""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel, TimeStampedModel


class NotificationSeverity(models.TextChoices):
    INFO = "INFO", _("Information")
    WARNING = "WARNING", _("Warning")
    CRITICAL = "CRITICAL", _("Critical")


class NotificationCode(models.TextChoices):
    """
    Stable machine codes for alert types.

    Codes rather than free text so the frontend can route, group and translate
    them, and so a deduplication window can suppress repeat alerts about the
    same condition without string matching.
    """

    LOW_STOCK = "LOW_STOCK", _("Low stock")
    OUT_OF_STOCK = "OUT_OF_STOCK", _("Out of stock")
    EXPIRING_SOON = "EXPIRING_SOON", _("Expiring medicines")
    EXPIRED = "EXPIRED", _("Expired medicines")
    PO_APPROVAL_REQUEST = "PO_APPROVAL_REQUEST", _("Purchase order approval request")
    PO_APPROVED = "PO_APPROVED", _("Purchase order approved")
    PO_REJECTED = "PO_REJECTED", _("Purchase order rejected")
    STOCK_DISCREPANCY = "STOCK_DISCREPANCY", _("Stock discrepancy")
    INVOICE_DUE = "INVOICE_DUE", _("Invoice due")
    INVOICE_OVERDUE = "INVOICE_OVERDUE", _("Invoice overdue")
    CREDIT_LIMIT_REACHED = "CREDIT_LIMIT_REACHED", _("Credit limit reached")
    AUDIT_CHAIN_BROKEN = "AUDIT_CHAIN_BROKEN", _("Audit integrity failure")
    LICENCE_EXPIRING = "LICENCE_EXPIRING", _("Customer licence expiring")
    SYSTEM_ANNOUNCEMENT = "SYSTEM_ANNOUNCEMENT", _("System announcement")


class Notification(TimeStampedModel):
    """
    A single alert delivered to one user.

    Per-recipient rows rather than a shared notification with read receipts:
    it keeps the "unread count" query trivial (the hottest query in any
    notification system) and lets each user dismiss independently.
    """

    recipient = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="notifications",
        verbose_name=_("recipient"),
    )
    code = models.CharField(
        _("code"), max_length=32, choices=NotificationCode.choices, db_index=True,
    )
    severity = models.CharField(
        _("severity"), max_length=10,
        choices=NotificationSeverity.choices, default=NotificationSeverity.INFO,
    )
    title = models.CharField(_("title"), max_length=200)
    body = models.TextField(_("body"), blank=True)
    link = models.CharField(
        _("link"), max_length=255, blank=True,
        help_text=_("Frontend route the notification points to."),
    )

    entity_type = models.CharField(max_length=100, blank=True)
    entity_id = models.CharField(max_length=64, blank=True)

    # Used to suppress repeat alerts about the same underlying condition
    # within a cooling-off window — without it, a daily expiry scan would
    # re-notify about the same batch every morning until it is dealt with.
    dedupe_key = models.CharField(max_length=200, blank=True, db_index=True)

    read_at = models.DateTimeField(_("read at"), null=True, blank=True)
    dismissed_at = models.DateTimeField(_("dismissed at"), null=True, blank=True)
    emailed_at = models.DateTimeField(_("emailed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["recipient", "read_at"], name="idx_notif_recipient_read"),
            models.Index(fields=["recipient", "-created_at"], name="idx_notif_recipient_date"),
            models.Index(fields=["dedupe_key", "-created_at"], name="idx_notif_dedupe"),
        ]

    def __str__(self) -> str:
        return f"{self.code} → {self.recipient.email}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def mark_read(self) -> None:
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at", "updated_at"])


class Announcement(BaseModel):
    """A broadcast message from administrators, shown to all or by role."""

    title_fr = models.CharField(_("title (French)"), max_length=200)
    title_en = models.CharField(_("title (English)"), max_length=200, blank=True)
    body_fr = models.TextField(_("body (French)"))
    body_en = models.TextField(_("body (English)"), blank=True)
    severity = models.CharField(
        _("severity"), max_length=10,
        choices=NotificationSeverity.choices, default=NotificationSeverity.INFO,
    )
    target_roles = models.ManyToManyField(
        "accounts.Role", blank=True, related_name="announcements",
        verbose_name=_("target roles"),
        help_text=_("Leave empty to broadcast to every user."),
    )
    starts_at = models.DateTimeField(_("visible from"), default=timezone.now)
    ends_at = models.DateTimeField(_("visible until"), null=True, blank=True)
    is_published = models.BooleanField(_("published"), default=False)

    class Meta:
        verbose_name = _("announcement")
        verbose_name_plural = _("announcements")
        ordering = ("-starts_at",)

    def __str__(self) -> str:
        return self.title_fr

    @property
    def is_visible(self) -> bool:
        now = timezone.now()
        return (
            self.is_published
            and self.starts_at <= now
            and (self.ends_at is None or self.ends_at > now)
        )
