"""
Session lifecycle and account-state gates.

These cover the controls that decide whether a token that was valid a moment
ago still is. Each test here corresponds to a way the system previously kept
honouring a credential it should have stopped honouring — a revoked session, a
suspended account, or a password the holder was required to change. They are
written against the API rather than the service layer because the gap in every
case was that the enforcement never reached the request path.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

PASSWORD = "TestPass!2026#Secure"


def _login(api_client, user):
    response = api_client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    return response.json()


class TestSessionRegistration:
    def test_login_opens_a_tracked_session(self, api_client, admin_user):
        """
        Without this row, every session-management feature is inert: the
        administrator's session list, "sign out everywhere", and the
        revocation performed on suspension and password change all operate on
        this table.
        """
        _login(api_client, admin_user)
        assert admin_user.sessions.count() == 1

        session = admin_user.sessions.get()
        assert session.revoked_at is None
        assert session.is_active

    def test_refresh_rotates_the_jti_and_keeps_one_session(
        self, api_client, admin_user
    ):
        """
        Rotation replaces the refresh token, so a session row still pointing
        at the old jti would become unrevocable — the holder keeps refreshing
        under a jti nothing tracks.
        """
        body = _login(api_client, admin_user)
        original_jti = admin_user.sessions.get().jti

        response = api_client.post(
            "/api/v1/auth/refresh/", {"refresh": body["refresh"]}, format="json"
        )
        assert response.status_code == 200
        assert admin_user.sessions.count() == 1
        assert admin_user.sessions.get().jti != original_jti

        # The reissued access token must still authenticate, otherwise
        # refreshing would sign the user out.
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
        assert api_client.get("/api/v1/catalog/medicines/").status_code == 200

    def test_logout_marks_the_session_revoked(self, api_client, admin_user):
        body = _login(api_client, admin_user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")
        api_client.post(
            "/api/v1/auth/logout/", {"refresh": body["refresh"]}, format="json"
        )
        assert admin_user.sessions.get().revoked_at is not None


class TestRevocationTakesEffectImmediately:
    def test_revoking_a_session_kills_the_access_token(self, api_client, admin_user):
        """
        The point of tracking sessions. Stock JWT authentication trusts a
        token for its full lifetime, so revocation would otherwise not bite
        until the access token expired.
        """
        from apps.accounts import services

        body = _login(api_client, admin_user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")
        assert api_client.get("/api/v1/catalog/medicines/").status_code == 200

        assert services.revoke_all_sessions(admin_user, reason="test") == 1
        assert api_client.get("/api/v1/catalog/medicines/").status_code == 401

    def test_revoked_session_cannot_be_refreshed_back(self, api_client, admin_user):
        """Revocation must not be undoable by presenting the old refresh token."""
        from apps.accounts import services

        body = _login(api_client, admin_user)
        services.revoke_all_sessions(admin_user, reason="test")

        response = api_client.post(
            "/api/v1/auth/refresh/", {"refresh": body["refresh"]}, format="json"
        )
        assert response.status_code == 401

    def test_password_change_ends_other_sessions(self, api_client, pharmacist):
        """
        A password change is often a response to suspected compromise, so the
        sessions opened with the old credential must not survive it.
        """
        from apps.accounts import services

        body = _login(api_client, pharmacist)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")

        services.set_password(pharmacist, "Another!Pass9#2026", actor=pharmacist)

        assert api_client.get("/api/v1/catalog/medicines/").status_code == 401


class TestSuspendedAccounts:
    def test_suspension_invalidates_a_live_token(self, auth_client, pharmacist):
        client = auth_client(pharmacist)
        assert client.get("/api/v1/catalog/medicines/").status_code == 200

        pharmacist.is_suspended = True
        pharmacist.save(update_fields=["is_suspended"])

        # Rejected at authentication, before the view is reached.
        assert client.get("/api/v1/catalog/medicines/").status_code == 401

    def test_deactivation_invalidates_a_live_token(self, auth_client, pharmacist):
        client = auth_client(pharmacist)
        pharmacist.is_active = False
        pharmacist.save(update_fields=["is_active"])
        assert client.get("/api/v1/catalog/medicines/").status_code in (401, 403)


class TestMustChangePassword:
    """
    The flag is set whenever a credential is known to someone other than the
    account holder. Until it is cleared, that shared credential must not be
    able to do the account's work — previously it was advisory only, enforced
    by a frontend redirect that any direct API call bypassed.
    """

    def test_pending_change_blocks_ordinary_endpoints(self, auth_client, pharmacist):
        pharmacist.must_change_password = True
        pharmacist.save(update_fields=["must_change_password"])

        client = auth_client(pharmacist)
        assert client.get("/api/v1/catalog/medicines/").status_code == 403

    def test_pending_change_leaves_an_escape_route(self, auth_client, pharmacist):
        """Blocking everything would strand the user with no way to comply."""
        pharmacist.must_change_password = True
        pharmacist.save(update_fields=["must_change_password"])

        client = auth_client(pharmacist)
        assert client.get("/api/v1/auth/me/").status_code == 200

        response = client.post(
            "/api/v1/auth/password/change/",
            {"current_password": PASSWORD, "new_password": "Brand!New9Pass#2026"},
            format="json",
        )
        assert response.status_code == 200

        pharmacist.refresh_from_db()
        assert pharmacist.must_change_password is False

    def test_administrator_reset_sets_the_flag(self, auth_client, admin_user, technician):
        """An administrator must never be left holding a working credential."""
        client = auth_client(admin_user)
        response = client.post(
            f"/api/v1/auth/users/{technician.pk}/reset-password/",
            {"new_password": "Reset!Pass9#2026"},
            format="json",
        )
        assert response.status_code == 200

        technician.refresh_from_db()
        assert technician.must_change_password is True


class TestSuperuserPermissionReporting:
    def test_superuser_without_roles_reports_full_catalogue(self, auth_client, db):
        """
        `has_perm_code` grants a superuser everything, so the list the UI gates
        on must say so. Reporting an empty list would hide every control from
        the one account whose requests the API would in fact accept.
        """
        from apps.accounts.models import User
        from apps.accounts.rbac import ALL_CODES

        superuser = User.objects.create_superuser(
            email="root@test.bi", password=PASSWORD,
            first_name="Root", last_name="User",
        )
        assert superuser.roles.count() == 0

        response = auth_client(superuser).get("/api/v1/auth/me/")
        assert sorted(response.json()["permissions"]) == sorted(ALL_CODES)


class TestPermissionListMatchesEnforcement:
    """
    The permission list drives what the UI offers; the API decides what it
    honours. When they disagree the user is shown a door that will not open —
    a menu entry that leads to "you do not have permission", which reads as a
    broken application rather than as a boundary.

    Account state is where they drifted apart: `effective_permissions()` unions
    active roles and never consults it, while the request path refuses these
    accounts before any codename is looked at.
    """

    def test_pending_password_change_reports_no_permissions(
        self, auth_client, pharmacist
    ):
        """
        The reachable case. `/auth/me/` is deliberately exempt from the
        password-change block, so it answers 200 while every other endpoint
        403s — previously handing back a full permission list that rendered a
        complete menu in which nothing worked.
        """
        pharmacist.must_change_password = True
        pharmacist.save(update_fields=["must_change_password"])

        client = auth_client(pharmacist)
        assert client.get("/api/v1/catalog/medicines/").status_code == 403

        response = client.get("/api/v1/auth/me/")
        assert response.status_code == 200
        assert response.json()["permissions"] == []

    def test_suspended_account_reports_no_permissions(self, pharmacist):
        """
        Asserted on the serializer rather than over HTTP: suspension is caught
        at authentication, so the request never reaches a view to be asked.
        The list must still agree with `has_perm_code`, which refuses a
        suspended account every codename it holds.
        """
        from apps.accounts.serializers import UserSerializer

        pharmacist.is_suspended = True
        pharmacist.save(update_fields=["is_suspended"])

        assert pharmacist.has_perm_code("catalog.view_medicine") is False
        assert UserSerializer(pharmacist).data["permissions"] == []

    def test_deactivated_account_reports_no_permissions(self, pharmacist):
        from apps.accounts.serializers import UserSerializer

        pharmacist.is_active = False
        pharmacist.save(update_fields=["is_active"])

        assert pharmacist.has_perm_code("catalog.view_medicine") is False
        assert UserSerializer(pharmacist).data["permissions"] == []

    def test_ordinary_account_still_reports_its_roles(self, auth_client, pharmacist):
        """The guard above must not cost a healthy account its permissions."""
        response = auth_client(pharmacist).get("/api/v1/auth/me/")
        assert response.status_code == 200

        reported = response.json()["permissions"]
        assert reported == sorted(pharmacist.effective_permissions())
        assert "catalog.view_medicine" in reported

    def test_every_reported_permission_is_one_the_api_honours(self, pharmacist):
        """
        The invariant itself, stated directly: for a healthy account the list
        the UI gates on and the check the API applies must agree code for code.
        """
        from apps.accounts.serializers import UserSerializer

        for code in UserSerializer(pharmacist).data["permissions"]:
            assert pharmacist.has_perm_code(code), code


class TestAuditTrailExport:
    """
    `core.export_auditlog` was defined, granted and declared on the viewset,
    but no route implemented it — the permission governed nothing.
    """

    def test_export_requires_the_permission(self, auth_client, technician, product):
        assert (
            auth_client(technician).get("/api/v1/core/audit-logs/export/").status_code
            == 403
        )

    def test_auditor_can_export_csv(self, auth_client, auditor, product):
        response = auth_client(auditor).get("/api/v1/core/audit-logs/export/")
        assert response.status_code == 200
        # BOM so Excel on a French locale detects UTF-8, as elsewhere.
        assert response.content[:3] == b"\xef\xbb\xbf"

    def test_export_is_itself_audited(self, auth_client, auditor, product):
        from apps.core.models import AuditAction, AuditLog

        auth_client(auditor).get("/api/v1/core/audit-logs/export/")
        assert AuditLog.objects.filter(
            action=AuditAction.EXPORT, entity_type="core.AuditLog"
        ).exists()

    def test_unsupported_format_is_refused(self, auth_client, auditor):
        response = auth_client(auditor).get(
            "/api/v1/core/audit-logs/export/", {"export": "exe"}
        )
        assert response.status_code in (400, 422)
