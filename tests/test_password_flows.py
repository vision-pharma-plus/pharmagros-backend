"""
End-to-end coverage of the three password-mutating paths.

Companion to test_user_create_password.py. That file covers creation; this one
covers self-service change, token-based reset, and administrator reset —
exercised through the HTTP API so serializer wiring, permissions and the
service layer are all in the path, since the creation bug lived precisely in
that wiring rather than in the service layer it bypassed.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.hashers import identify_hasher
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import Role, User

pytestmark = pytest.mark.django_db

OLD = "OldPass!2026#Aa"
NEW = "NewPass!2026#Bb"


def assert_hashed(user, raw: str | None = None):
    """Assert the stored value is a real hash, not plaintext."""
    assert user.password
    if raw is not None:
        assert user.password != raw, "password stored in plaintext"
    identify_hasher(user.password)
    assert user.has_usable_password()


@pytest.fixture
def user():
    return User.objects.create_user(
        email="flow@pharmagros.bi", password=OLD,
        first_name="Flow", last_name="User",
    )


@pytest.fixture
def admin():
    admin = User.objects.create_user(
        email="admin-flow@pharmagros.bi", password=OLD,
        first_name="Admin", last_name="Flow",
    )
    role = Role.objects.get(code="system-administrator")
    admin.roles.set([role])
    return admin


def auth(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# -- Self-service change ----------------------------------------------------


class TestPasswordChange:
    def test_change_hashes_and_new_password_works(self, user):
        response = auth(user).post(
            reverse("password-change"),
            {"current_password": OLD, "new_password": NEW},
            format="json",
        )
        assert response.status_code == 200, response.data

        user.refresh_from_db()
        assert_hashed(user, NEW)
        assert user.check_password(NEW)
        assert not user.check_password(OLD), "old password still valid"

    def test_wrong_current_password_is_rejected(self, user):
        response = auth(user).post(
            reverse("password-change"),
            {"current_password": "NotThePass!1A", "new_password": NEW},
            format="json",
        )
        assert response.status_code == 400

        user.refresh_from_db()
        assert user.check_password(OLD), "password changed despite bad credential"

    def test_weak_new_password_is_rejected(self, user):
        response = auth(user).post(
            reverse("password-change"),
            {"current_password": OLD, "new_password": "password"},
            format="json",
        )
        assert response.status_code == 400

        user.refresh_from_db()
        assert user.check_password(OLD)

    def test_reuse_of_recent_password_is_blocked(self, user):
        client = auth(user)
        assert client.post(
            reverse("password-change"),
            {"current_password": OLD, "new_password": NEW},
            format="json",
        ).status_code == 200

        # Attempt to swing back to the original.
        response = client.post(
            reverse("password-change"),
            {"current_password": NEW, "new_password": OLD},
            format="json",
        )
        assert response.status_code == 400, "history policy did not block reuse"

    def test_anonymous_cannot_change_password(self, user):
        response = APIClient().post(
            reverse("password-change"),
            {"current_password": OLD, "new_password": NEW},
            format="json",
        )
        assert response.status_code in (401, 403)


# -- Token-based reset ------------------------------------------------------


class TestPasswordReset:
    def _payload(self, user, password=NEW):
        return {
            "uid": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": default_token_generator.make_token(user),
            "new_password": password,
        }

    def test_reset_confirm_hashes_and_new_password_works(self, user):
        response = APIClient().post(
            reverse("password-reset-confirm"), self._payload(user), format="json",
        )
        assert response.status_code == 200, response.data

        user.refresh_from_db()
        assert_hashed(user, NEW)
        assert user.check_password(NEW)
        assert not user.check_password(OLD)

    def test_reset_token_is_single_use(self, user):
        payload = self._payload(user)
        assert APIClient().post(
            reverse("password-reset-confirm"), payload, format="json",
        ).status_code == 200

        # The token embeds the old password hash, so it must not work twice.
        again = APIClient().post(
            reverse("password-reset-confirm"), payload, format="json",
        )
        assert again.status_code == 400, "reset token was reusable"

    def test_invalid_token_is_rejected(self, user):
        payload = self._payload(user)
        payload["token"] = "not-a-valid-token"

        response = APIClient().post(
            reverse("password-reset-confirm"), payload, format="json",
        )
        assert response.status_code == 400

        user.refresh_from_db()
        assert user.check_password(OLD)

    def test_reset_request_does_not_enumerate_accounts(self):
        known = APIClient().post(
            reverse("password-reset"), {"email": "flow@pharmagros.bi"}, format="json",
        )
        unknown = APIClient().post(
            reverse("password-reset"), {"email": "nobody@pharmagros.bi"}, format="json",
        )
        assert known.status_code == unknown.status_code == 200
        assert known.data == unknown.data


# -- Administrator reset ----------------------------------------------------


class TestAdminReset:
    def test_admin_reset_hashes_and_forces_change(self, admin, user):
        response = auth(admin).post(
            reverse("user-reset-password", args=[user.pk]),
            {"new_password": NEW},
            format="json",
        )
        assert response.status_code == 200, response.data

        user.refresh_from_db()
        assert_hashed(user, NEW)
        assert user.check_password(NEW)
        assert user.must_change_password is True

    def test_admin_reset_rejects_weak_password(self, admin, user):
        response = auth(admin).post(
            reverse("user-reset-password", args=[user.pk]),
            {"new_password": "password"},
            format="json",
        )
        assert response.status_code == 400

        user.refresh_from_db()
        assert user.check_password(OLD)

    def test_admin_reset_requires_a_password(self, admin, user):
        response = auth(admin).post(
            reverse("user-reset-password", args=[user.pk]), {}, format="json",
        )
        assert response.status_code == 400

    def test_non_admin_cannot_reset_another_user(self, user):
        target = User.objects.create_user(
            email="target@pharmagros.bi", password=OLD,
            first_name="T", last_name="U",
        )
        response = auth(user).post(
            reverse("user-reset-password", args=[target.pk]),
            {"new_password": NEW},
            format="json",
        )
        assert response.status_code in (401, 403)

        target.refresh_from_db()
        assert target.check_password(OLD)


class TestCurrentPasswordReuse:
    """
    The in-force password must be rejected as a "new" password.

    It is not in PasswordHistory at validation time (it is copied there only
    after validation succeeds), so it needs its own check.
    """

    def test_cannot_change_password_to_the_current_one(self, user):
        response = auth(user).post(
            reverse("password-change"),
            {"current_password": OLD, "new_password": OLD},
            format="json",
        )
        assert response.status_code == 400, "current password accepted as new"

    def test_admin_cannot_reset_to_the_users_current_password(self, admin, user):
        response = auth(admin).post(
            reverse("user-reset-password", args=[user.pk]),
            {"new_password": OLD},
            format="json",
        )
        assert response.status_code == 400

    def test_reset_confirm_rejects_the_current_password(self, user):
        payload = {
            "uid": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": default_token_generator.make_token(user),
            "new_password": OLD,
        }
        response = APIClient().post(
            reverse("password-reset-confirm"), payload, format="json",
        )
        assert response.status_code == 400

    def test_a_genuinely_new_password_is_still_accepted(self, user):
        """Guards against the check over-reaching and blocking valid changes."""
        response = auth(user).post(
            reverse("password-change"),
            {"current_password": OLD, "new_password": NEW},
            format="json",
        )
        assert response.status_code == 200, response.data

        user.refresh_from_db()
        assert user.check_password(NEW)
