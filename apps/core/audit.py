"""
Audit recording service.

Everything that writes to the audit trail goes through `record()`. Callers do
not construct AuditLog rows directly, so the hash chain cannot be bypassed by
accident.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Model

from .context import get_context
from .models import AuditAction, AuditLog

logger = logging.getLogger("audit")

# Never persist these to the audit trail, in any entity.
SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "last_password_hash",
        "token",
        "refresh_token",
        "access_token",
        "secret",
        "totp_secret",
        "mfa_secret",
        "api_key",
        "reset_token",
        "session_key",
    }
)

REDACTED = "***REDACTED***"


def _serialise_value(value: Any) -> Any:
    """JSON-safe representation that preserves decimal precision as string."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        # str() not float() — the whole point of Decimal is not round-tripping
        # through binary floating point.
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Model):
        return str(value.pk)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return [_serialise_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialise_value(v) for k, v in value.items()}
    return str(value)


def snapshot(instance: Model, fields: Iterable[str] | None = None) -> dict:
    """
    Capture a model instance's field values for the audit trail.

    Only concrete local fields are captured; related managers are skipped to
    avoid triggering queries (and unbounded payloads) during a save signal.
    """
    data: dict[str, Any] = {}
    for field in instance._meta.concrete_fields:
        name = field.name
        if fields is not None and name not in fields:
            continue
        if name in SENSITIVE_FIELDS:
            data[name] = REDACTED
            continue
        try:
            data[name] = _serialise_value(getattr(instance, field.attname, None))
        except Exception:  # pragma: no cover - defensive
            data[name] = "<unreadable>"
    return data


def diff(before: dict | None, after: dict | None) -> list[str]:
    """Names of fields whose values changed between two snapshots."""
    if not before or not after:
        return sorted((after or before or {}).keys())
    return sorted(k for k in after if before.get(k) != after.get(k))


def _last_hash() -> str:
    """
    Fetch the hash of the most recent audit entry.

    Locks the tail row so two concurrent writers cannot both read the same
    predecessor and fork the chain. This serialises audit writes, which is an
    acceptable cost — audit volume is far below transactional volume, and a
    forked chain would be unverifiable.

    Expressed through the ORM rather than raw SQL so the lock is emitted only
    on backends that support it. SQLite (used for offline checks and fast unit
    tests) has no SELECT ... FOR UPDATE, but it also serialises writers at the
    database level, so the invariant still holds there.
    """
    queryset = AuditLog.objects.order_by("-id").values_list("entry_hash", flat=True)
    if connection.features.has_select_for_update:
        queryset = queryset.select_for_update()

    row = queryset.first()
    return row or ""


@transaction.atomic
def record(
    action: str,
    entity_type: str,
    *,
    entity_id: str | None = None,
    entity_label: str = "",
    previous_value: dict | None = None,
    new_value: dict | None = None,
    changed_fields: list[str] | None = None,
    notes: str = "",
    actor=None,
) -> AuditLog:
    """Write one audit entry, extending the hash chain."""
    ctx = get_context()
    resolved_actor = actor if actor is not None else ctx.user

    entry = AuditLog(
        actor=resolved_actor if getattr(resolved_actor, "pk", None) else None,
        actor_username=(
            resolved_actor.get_username() if resolved_actor is not None else ctx.username
        ),
        actor_role=(
            getattr(resolved_actor, "primary_role_name", "")
            if resolved_actor is not None
            else ctx.role
        ),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else "",
        entity_label=entity_label[:255],
        previous_value=previous_value,
        new_value=new_value,
        changed_fields=changed_fields or [],
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
        request_id=ctx.request_id,
        notes=notes,
        previous_hash=_last_hash(),
    )
    entry.save()

    logger.info(
        "audit",
        extra={
            "audit_action": action,
            "entity_type": entity_type,
            "entity_id": entry.entity_id,
            "actor": entry.actor_username,
            "request_id": entry.request_id,
        },
    )
    return entry


def record_model_change(
    instance: Model,
    action: str,
    *,
    before: dict | None = None,
    notes: str = "",
) -> AuditLog:
    """Convenience wrapper for CRUD auditing of a model instance."""
    after = snapshot(instance) if action != AuditAction.DELETE else None
    return record(
        action,
        instance._meta.label,
        entity_id=str(instance.pk),
        entity_label=str(instance)[:255],
        previous_value=before,
        new_value=after,
        changed_fields=diff(before, after),
        notes=notes,
    )


def verify_chain(start_id: int = 0, limit: int | None = None) -> dict:
    """
    Re-walk the hash chain and report the first break, if any.

    Returns a summary rather than raising, so the nightly task can alert with
    detail instead of just failing.
    """
    qs = AuditLog.objects.filter(id__gt=start_id).order_by("id")
    if limit:
        qs = qs[:limit]

    checked = 0
    expected_previous: str | None = None
    for entry in qs.iterator(chunk_size=1000):
        if expected_previous is not None and entry.previous_hash != expected_previous:
            return {
                "valid": False,
                "checked": checked,
                "broken_at_id": entry.id,
                "reason": "previous_hash does not match preceding entry",
            }
        if entry.compute_hash() != entry.entry_hash:
            return {
                "valid": False,
                "checked": checked,
                "broken_at_id": entry.id,
                "reason": "entry contents do not match stored hash",
            }
        expected_previous = entry.entry_hash
        checked += 1

    return {"valid": True, "checked": checked, "broken_at_id": None, "reason": ""}
