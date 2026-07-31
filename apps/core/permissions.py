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
