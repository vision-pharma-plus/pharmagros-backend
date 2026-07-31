"""
Regression tests for user creation via the admin API.

The original defect: UserCreateSerializer declared `password` as a plain
write-only field with no create() override, so ModelSerializer stored the raw
string in the password column. The account was created successfully but could
never authenticate, because check_password() compares against a hash.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import identify_hasher

from apps.accounts.serializers import UserCreateSerializer, _generate_temporary_password

pytestmark = pytest.mark.django_db


def assert_hashed(user, raw: str | None = None):
    """
    Assert the stored value is a real password hash.

    Checked via identify_hasher rather than an algorithm prefix: the test
    settings swap Argon2 for MD5 to keep the suite fast, so asserting on
    "argon2$" would test the settings rather than the serializer.
    """
    assert user.password, "no password stored"
    if raw is not None:
        assert user.password != raw, "password stored in plaintext"
    identify_hasher(user.password)  # raises ValueError if not a recognised hash
    assert user.has_usable_password()


def _create(**overrides):
    data = {
        "email": "newuser@pharmagros.bi",
        "first_name": "New",
        "last_name": "User",
        "language": "fr",
        "is_active": True,
    }
    data.update(overrides)
    serializer = UserCreateSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.save()


def test_password_is_hashed_not_stored_raw():
    raw = "Testing@654321"
    user = _create(password=raw)

    assert_hashed(user, raw)
    assert user.check_password(raw)


def test_new_user_can_authenticate_with_created_password():
    """The actual reported symptom: login with the password set at creation."""
    raw = "Testing@654321"
    _create(password=raw)

    assert authenticate(request=None, username="newuser@pharmagros.bi", password=raw) is not None


def test_login_is_case_insensitive_on_email():
    raw = "Testing@654321"
    _create(email="Mixed.Case@pharmagros.bi", password=raw)

    assert authenticate(request=None, username="mixed.case@pharmagros.bi", password=raw) is not None


def test_blank_password_generates_a_usable_temporary_credential():
    user = _create(password="")

    assert_hashed(user)
    assert not user.check_password("")
    assert user.must_change_password is True


def test_password_omitted_entirely_still_hashes():
    user = _create()

    assert_hashed(user)


def test_created_user_must_change_password():
    user = _create(password="Testing@654321")
    assert user.must_change_password is True
    assert user.password_changed_at is not None


def test_roles_are_assigned_after_creation():
    from apps.accounts.models import Role

    role = Role.objects.filter(is_active=True).first()
    assert role is not None, "seed_rbac should provide roles"

    user = _create(roles=[role.pk])
    assert list(user.roles.values_list("code", flat=True)) == [role.code]


def test_weak_password_is_rejected():
    serializer = UserCreateSerializer(
        data={
            "email": "weak@pharmagros.bi",
            "first_name": "Weak",
            "last_name": "User",
            "language": "fr",
            "password": "password",
        }
    )
    assert not serializer.is_valid()
    assert "password" in serializer.errors


@pytest.mark.parametrize("_run", range(25))
def test_generated_temporary_password_satisfies_policy(_run):
    """Generated credentials must pass the project's own validators."""
    from django.contrib.auth.password_validation import validate_password

    password = _generate_temporary_password()
    assert len(password) >= 12
    validate_password(password)  # raises if the policy is not met
