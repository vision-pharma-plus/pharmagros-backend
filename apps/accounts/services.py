"""Account service layer: password lifecycle, sessions, role assignment."""

from __future__ import annotations

from datetime import UTC, datetime

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record
from apps.core.exceptions import BusinessRuleViolation
from apps.core.models import AuditAction

from .models import PasswordHistory, User, UserSession

PASSWORD_HISTORY_DEPTH = 5


@transaction.atomic
def set_password(user: User, raw_password: str, *, actor: User | None = None,
                 require_change: bool = False) -> User:
    """
    Change a password with policy enforcement and history tracking.

    The old hash is pushed to history *before* the new one is set, and history
    is trimmed to the configured depth so the table cannot grow unbounded.
    """
    validate_password(raw_password, user=user)

    if user.password:
        PasswordHistory.objects.create(user=user, password_hash=user.password)
        # Trim beyond the retention depth.
        stale_ids = list(
            user.password_history.order_by("-created_at")
            .values_list("id", flat=True)[PASSWORD_HISTORY_DEPTH:]
        )
        if stale_ids:
            PasswordHistory.objects.filter(id__in=stale_ids).delete()

    user.set_password(raw_password)
    user.password_changed_at = timezone.now()
    user.must_change_password = require_change
    user.save(
        update_fields=["password", "password_changed_at", "must_change_password", "updated_at"]
    )

    # Changing a password invalidates every existing session: a password
    # change is often a response to suspected compromise, and leaving old
    # refresh tokens live would defeat the point.
    revoke_all_sessions(user, reason="password changed")

    record(
        AuditAction.PASSWORD_CHANGE,
        User._meta.label,
        entity_id=str(user.pk),
        entity_label=user.email,
        notes=(
            "Password changed by administrator"
            if actor and actor.pk != user.pk
            else "Password changed by user"
        ),
        actor=actor or user,
    )
    return user


@transaction.atomic
def assign_roles(user: User, role_ids: list, *, actor: User) -> User:
    """
    Replace a user's roles.

    Privilege changes are among the most security-relevant events in the
    system, so the before/after role sets are captured explicitly rather than
    relying on generic model diffing.
    """
    from .models import Role

    before = sorted(user.roles.values_list("code", flat=True))
    roles = list(Role.objects.filter(id__in=role_ids, is_active=True))

    if len(roles) != len(set(role_ids)):
        raise BusinessRuleViolation(
            _("One or more of the selected roles do not exist or are inactive."),
            code="invalid_role",
        )

    user.roles.set(roles)
    user.invalidate_permission_cache()
    after = sorted(r.code for r in roles)

    record(
        AuditAction.PERMISSION_CHANGE,
        User._meta.label,
        entity_id=str(user.pk),
        entity_label=user.email,
        previous_value={"roles": before},
        new_value={"roles": after},
        changed_fields=["roles"],
        notes=f"Roles changed from {before} to {after}",
        actor=actor,
    )
    return user


def register_session(user: User, *, jti: str, expires_at, ip: str | None,
                     user_agent: str) -> UserSession:
    return UserSession.objects.create(
        user=user, jti=jti, expires_at=expires_at, ip_address=ip,
        user_agent=(user_agent or "")[:512],
    )


def rotate_session(old_jti: str, token_data: dict, *, ip: str | None,
                   user_agent: str) -> None:
    """
    Re-point a session at the refresh token that replaced its own.

    SIMPLE_JWT rotates and blacklists on every refresh, so the jti recorded at
    login stops being the live one after the first refresh. Leaving the row
    pointing at the blacklisted jti would make the session unrevocable — the
    holder keeps refreshing under a jti nothing tracks. `last_seen_at` is
    updated here too, which is what makes the administrator's session list
    show genuine activity rather than login time.
    """
    from django.conf import settings
    from rest_framework_simplejwt.tokens import RefreshToken

    new_refresh = token_data.get("refresh")
    if not new_refresh:
        # Rotation disabled: the original token still stands, so only touch
        # the activity timestamp.
        UserSession.objects.filter(jti=old_jti).update(last_seen_at=timezone.now())
        return

    try:
        token = RefreshToken(new_refresh)
    except Exception:  # pragma: no cover - the token was just minted
        return

    new_jti = token[settings.SIMPLE_JWT.get("JTI_CLAIM", "jti")]
    token["session_jti"] = new_jti

    UserSession.objects.filter(jti=old_jti).update(
        jti=new_jti,
        last_seen_at=timezone.now(),
        expires_at=datetime.fromtimestamp(token["exp"], tz=UTC),
        ip_address=ip,
        user_agent=(user_agent or "")[:512],
    )

    # The client must receive tokens carrying the session claim, otherwise the
    # next request authenticates with no session to check.
    token_data["refresh"] = str(token)
    access = token.access_token
    access["session_jti"] = new_jti
    token_data["access"] = str(access)


def revoke_session(session: UserSession, *, reason: str = "") -> None:
    """Revoke one session and blacklist its refresh token."""
    if session.revoked_at is not None:
        return
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at"])
    _blacklist(session.jti)

    record(
        AuditAction.LOGOUT,
        UserSession._meta.label,
        entity_id=str(session.pk),
        entity_label=session.user.email,
        notes=f"Session revoked: {reason}" if reason else "Session revoked",
    )


def revoke_all_sessions(user: User, *, reason: str = "", exclude_jti: str | None = None) -> int:
    """Revoke every active session for a user. Returns the count revoked."""
    sessions = user.sessions.filter(revoked_at__isnull=True, expires_at__gt=timezone.now())
    if exclude_jti:
        sessions = sessions.exclude(jti=exclude_jti)

    count = 0
    for session in sessions:
        revoke_session(session, reason=reason)
        count += 1
    return count


def _blacklist(jti: str) -> None:
    """
    Blacklist a refresh token by its JTI.

    Wrapped defensively: a token already rotated out of OutstandingToken is
    not an error condition, and failing here would block the surrounding
    security action (logout, password change) from completing.
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        token = OutstandingToken.objects.filter(jti=jti).first()
        if token:
            BlacklistedToken.objects.get_or_create(token=token)
    except Exception:  # pragma: no cover - blacklist app may be unmigrated
        pass


def record_login(user: User, *, ip: str | None, success: bool = True,
                 notes: str = "") -> None:
    if success:
        user.last_login = timezone.now()
        user.last_login_ip = ip
        user.save(update_fields=["last_login", "last_login_ip"])

    record(
        AuditAction.LOGIN if success else AuditAction.LOGIN_FAILED,
        User._meta.label,
        entity_id=str(user.pk) if user and user.pk else "",
        entity_label=user.email if user else "",
        notes=notes,
        actor=user if success else None,
    )
