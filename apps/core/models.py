"""
Core abstract models and the immutable audit log.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """Creation/modification timestamps for every business entity."""

    created_at = models.DateTimeField(_("created at"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


class ActorStampedModel(models.Model):
    """Records which user created and last modified a row."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_created",
        null=True,
        blank=True,
        editable=False,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_updated",
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.filter(deleted_at__isnull=False)

    def delete(self):
        """Bulk soft-delete. Hard deletion requires .hard_delete()."""
        return self.update(deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class SoftDeleteManager(models.Manager):
    """
    Default manager that hides soft-deleted rows.

    Financial and pharmaceutical records are never physically removed — an
    invoice or stock movement that vanishes breaks traceability and would
    fail a regulatory inspection. `all_objects` remains available for admin
    and audit contexts that legitimately need to see deleted rows.
    """

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).alive()


class SoftDeleteModel(models.Model):
    deleted_at = models.DateTimeField(
        _("deleted at"), null=True, blank=True, db_index=True, editable=False
    )
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_deleted",
        null=True,
        blank=True,
        editable=False,
    )

    objects = SoftDeleteManager()
    all_objects = SoftDeleteQuerySet.as_manager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False, actor=None):
        self.deleted_at = timezone.now()
        self.deleted_by = actor
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"])

    def restore(self):
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"])

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class BaseModel(TimeStampedModel, ActorStampedModel, SoftDeleteModel):
    """
    Standard base for business entities.

    Uses a UUID primary key. Sequential integer PKs leak business volume
    (a competitor reading invoice #4712 learns your throughput) and make
    record enumeration trivial. Human-facing identifiers are separate,
    explicitly-formatted document numbers.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditAction(models.TextChoices):
    CREATE = "CREATE", _("Create")
    UPDATE = "UPDATE", _("Update")
    DELETE = "DELETE", _("Delete")
    LOGIN = "LOGIN", _("Login")
    LOGIN_FAILED = "LOGIN_FAILED", _("Failed login")
    LOGOUT = "LOGOUT", _("Logout")
    LOCKOUT = "LOCKOUT", _("Account lockout")
    PASSWORD_CHANGE = "PASSWORD_CHANGE", _("Password change")
    PASSWORD_RESET = "PASSWORD_RESET", _("Password reset")
    PERMISSION_CHANGE = "PERMISSION_CHANGE", _("Permission change")
    APPROVE = "APPROVE", _("Approval")
    REJECT = "REJECT", _("Rejection")
    CANCEL = "CANCEL", _("Cancellation")
    POST = "POST", _("Posting")
    PRINT = "PRINT", _("Print / reprint")
    EXPORT = "EXPORT", _("Data export")
    STOCK_MOVEMENT = "STOCK_MOVEMENT", _("Stock movement")
    PRICE_CHANGE = "PRICE_CHANGE", _("Price change")


class AuditLog(models.Model):
    """
    Append-only audit trail.

    Immutability is enforced at three layers, because application-level
    discipline alone is a convention rather than a control:

      1. Python — save() rejects updates, delete() raises.
      2. PostgreSQL — triggers reject UPDATE and DELETE on this table
         (see migration 0002). This holds even for code paths that bypass
         the ORM, such as raw SQL or a careless data migration.
      3. Hash chain — each row commits to its predecessor's hash, so a
         privileged actor who disables the triggers and edits history still
         leaves a detectable break. `verify_audit_chain` re-walks the chain
         nightly.

    Layer 3 is what makes the trail *tamper-evident* rather than merely
    *tamper-resistant*, which is the standard a pharmaceutical inspection
    actually applies.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    timestamp = models.DateTimeField(default=timezone.now, db_index=True, editable=False)

    # Denormalised actor identity: the FK may be nulled if a user row is ever
    # removed, but the audit line must still say who acted. Keeping the
    # username and email as text preserves the record independently.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
        editable=False,
    )
    actor_username = models.CharField(max_length=254, blank=True, editable=False)
    actor_role = models.CharField(max_length=100, blank=True, editable=False)

    action = models.CharField(
        max_length=32, choices=AuditAction.choices, db_index=True, editable=False
    )
    entity_type = models.CharField(max_length=100, db_index=True, editable=False)
    entity_id = models.CharField(max_length=64, db_index=True, blank=True, editable=False)
    entity_label = models.CharField(max_length=255, blank=True, editable=False)

    previous_value = models.JSONField(null=True, blank=True, editable=False)
    new_value = models.JSONField(null=True, blank=True, editable=False)
    changed_fields = models.JSONField(default=list, blank=True, editable=False)

    ip_address = models.GenericIPAddressField(null=True, blank=True, editable=False)
    user_agent = models.CharField(max_length=512, blank=True, editable=False)
    request_id = models.CharField(max_length=64, blank=True, db_index=True, editable=False)

    notes = models.TextField(blank=True, editable=False)

    # Tamper-evidence chain
    previous_hash = models.CharField(max_length=64, blank=True, editable=False)
    entry_hash = models.CharField(max_length=64, blank=True, db_index=True, editable=False)

    class Meta:
        verbose_name = _("audit log entry")
        verbose_name_plural = _("audit log")
        ordering = ("-timestamp", "-id")
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["actor", "-timestamp"]),
            models.Index(fields=["action", "-timestamp"]),
        ]

    def __str__(self) -> str:
        return f"[{self.timestamp:%Y-%m-%d %H:%M:%S}] {self.actor_username} {self.action} {self.entity_type}"

    # -- immutability ------------------------------------------------------
    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError(
                "Audit entries are immutable and cannot be modified once written."
            )
        self.entry_hash = self.compute_hash()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Audit entries are immutable and cannot be deleted.")

    # -- hash chain --------------------------------------------------------
    def canonical_payload(self) -> str:
        """
        Deterministic serialisation of the fields the hash commits to.

        sort_keys and a fixed separator set matter: any nondeterminism here
        would produce spurious chain-verification failures.
        """
        return json.dumps(
            {
                "uuid": str(self.uuid),
                "timestamp": self.timestamp.isoformat(),
                "actor": self.actor_username,
                "action": self.action,
                "entity_type": self.entity_type,
                "entity_id": self.entity_id,
                "previous_value": self.previous_value,
                "new_value": self.new_value,
                "ip": self.ip_address,
                "previous_hash": self.previous_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()


# Re-exported so Django's app registry discovers the sequence model, which
# lives in numbering.py alongside the allocation logic it belongs with.
from .numbering import DocumentSequence, SequenceScope  # noqa: E402,F401

__all__ = [
    "TimeStampedModel",
    "ActorStampedModel",
    "SoftDeleteModel",
    "SoftDeleteManager",
    "SoftDeleteQuerySet",
    "BaseModel",
    "AuditAction",
    "AuditLog",
    "DocumentSequence",
    "SequenceScope",
]
