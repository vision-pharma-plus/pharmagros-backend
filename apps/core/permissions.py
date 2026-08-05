"""
API-layer authorisation primitives.

The RBAC model itself lives in apps.accounts; this module provides the DRF
permission classes that enforce it at the API boundary.
"""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission


class HasPermission(BasePermission):
    """
    Checks a codename declared on the view.

    Usage:
        class MedicineViewSet(...):
            required_permissions = {
                "list": "catalog.view_medicine",
                "create": "catalog.add_medicine",
            }

    Deny-by-default: an action with no declared permission is refused rather
    than allowed. Forgetting to declare a permission should fail closed, not
    silently expose an endpoint.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        # A view setting `permission_classes = [HasPermission]` replaces
        # DEFAULT_PERMISSION_CLASSES outright, so the account-state gates must
        # be re-applied here rather than relying on the global default.
        if getattr(user, "is_suspended", False):
            return False
        if not PasswordChangeNotPending().has_permission(request, view):
            return False
        if user.is_superuser:
            return True

        required = getattr(view, "required_permissions", None)
        if required is None:
            return False

        if isinstance(required, str):
            codename = required
        else:
            action = getattr(view, "action", None) or request.method.lower()
            if action not in required:
                default = required.get("default")
                if default is None:
                    return False
                codename = default
            else:
                codename = required[action]

        if codename is None:  # explicit opt-out, e.g. "any authenticated user"
            return True

        return user.has_perm_code(codename)


class PasswordChangeNotPending(BasePermission):
    """
    Blocks a user who is required to change their password.

    `must_change_password` is set whenever a credential is known to someone
    other than the account holder — an administrator-set password, a reset, or
    a repaired plaintext row. Until it is changed, that credential is shared,
    so the session it opened must not be able to do the account's work. The
    flag was previously advisory: the login response reported it and the
    frontend redirected, but the API accepted every request regardless, so
    anyone calling the API directly could work indefinitely on a password the
    administrator who issued it also knows.

    The password-change and identity endpoints stay reachable, otherwise the
    user could not clear the flag.
    """

    message = "You must change your password before continuing."

    # Paths a user with a pending password change may still reach, so they can
    # complete the change and the frontend can render the form.
    EXEMPT_SUFFIXES = (
        "/auth/password/change/",
        "/auth/me/",
        "/auth/logout/",
        "/auth/language/",
        "/auth/refresh/",
    )

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return True  # authentication itself is enforced elsewhere
        if not getattr(user, "must_change_password", False):
            return True
        return request.path.endswith(self.EXEMPT_SUFFIXES)


class ReadOnly(BasePermission):
    """Allows only safe methods. Combined with HasPermission for auditors."""

    def has_permission(self, request, view) -> bool:
        return request.method in SAFE_METHODS


class IsNotLocked(BasePermission):
    """Blocks users whose account is administratively suspended."""

    message = "This account is suspended. Please contact your administrator."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and not user.is_suspended)
