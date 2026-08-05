import secrets
from datetime import UTC, datetime

from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Permission, Role, User, UserSession


class PermissionSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)

    class Meta:
        model = Permission
        fields = ["id", "code", "name", "name_fr", "name_en", "module", "is_sensitive"]
        read_only_fields = fields


class RoleSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    permission_codes = serializers.SerializerMethodField()
    user_count = serializers.IntegerField(source="users.count", read_only=True)

    class Meta:
        model = Role
        fields = [
            "id", "code", "name", "name_fr", "name_en",
            "description", "description_fr", "description_en",
            "permissions", "permission_codes", "inherits_from",
            "is_system", "is_active", "user_count",
        ]
        read_only_fields = ["id", "is_system"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Both name columns are non-blank on the model, but a client supplies
        # only the one language its user typed — `save()` back-fills the
        # other. Same accommodation the catalogue and accounting make.
        for field in ("name_fr", "name_en"):
            self.fields[field].required = False

    def get_permission_codes(self, obj) -> list[str]:
        """Effective permissions, including everything inherited."""
        return sorted(obj.effective_permissions())


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    role_codes = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "full_name", "phone",
            "employee_code", "job_title", "language",
            "roles", "role_codes", "permissions",
            "is_active", "is_staff", "is_suspended", "mfa_enabled",
            "must_change_password", "last_login", "created_at",
        ]
        read_only_fields = [
            "id", "last_login", "created_at", "is_staff", "must_change_password",
        ]

    def get_role_codes(self, obj) -> list[str]:
        return list(obj.roles.values_list("code", flat=True))

    def get_permissions(self, obj) -> list[str]:
        """
        The user's effective permission codes.

        Sent to the frontend so the UI can hide actions the user cannot
        perform. This is a usability affordance only — every one of these is
        independently enforced server-side, because a hidden button is not a
        security control.

        A superuser is reported as holding the whole catalogue, matching what
        `has_perm_code` grants them. Deriving this from role membership alone
        would hand a superuser with no roles an empty permission list, hiding
        every menu and button from the one account that can in fact use them —
        which is how an administrator locks themselves out of a UI whose API
        would have answered them.
        """
        if obj.is_superuser:
            from .rbac import ALL_CODES

            return sorted(ALL_CODES)
        return sorted(obj.effective_permissions())


def _generate_temporary_password(length: int = 16) -> str:
    """
    Build a temporary password that satisfies ComplexityValidator by
    construction.

    Drawing every character from one pooled alphabet would occasionally omit a
    required class and fail the project's own policy, so one character of each
    class is placed first and the remainder shuffled in. secrets.SystemRandom
    keeps the shuffle cryptographically sourced.
    """
    symbols = "!@#$%^&*()-_=+"
    required = [
        get_random_string(1, "ABCDEFGHJKLMNPQRSTUVWXYZ"),
        get_random_string(1, "abcdefghijkmnpqrstuvwxyz"),
        get_random_string(1, "23456789"),
        get_random_string(1, symbols),
    ]
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789" + symbols
    chars = required + list(get_random_string(max(length, 12) - len(required), alphabet))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "phone", "employee_code",
            "job_title", "language", "roles", "is_active", "password",
        ]

    def validate_password(self, value):
        if value:
            validate_password(value)
        return value

    def create(self, validated_data):
        """
        Create a user with a *hashed* password.

        ModelSerializer's default create() would pass the raw password straight
        to User.objects.create(), storing it verbatim in the password column.
        check_password() then compares the submitted password against what it
        treats as a hash, which never matches — so the account is created but
        cannot log in.

        Roles are M2M and must be set after the row exists.
        """
        roles = validated_data.pop("roles", [])
        raw_password = validated_data.pop("password", "") or ""

        # A blank password means "generate a temporary one", which the admin
        # form offers explicitly. The generated value is never returned, so the
        # administrator sends the user through password reset to claim it —
        # keeping the credential out of the response body and the audit log.
        if not raw_password:
            raw_password = _generate_temporary_password()

        user = User.objects.create_user(password=raw_password, **validated_data)

        if roles:
            user.roles.set(roles)

        # Any password the administrator chose is known to someone other than
        # the account holder, so it is valid exactly once.
        user.must_change_password = True
        user.password_changed_at = timezone.now()
        user.save(update_fields=["must_change_password", "password_changed_at", "updated_at"])
        return user


class TokenObtainSerializer(TokenObtainPairSerializer):
    """
    Login serializer that embeds identity and language in the token response.

    Returning the profile alongside the tokens saves the frontend a round trip
    on every login, which matters on the intermittent connectivity typical of
    the deployment environment.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Claims kept minimal: a JWT is readable by anyone holding it, so no
        # permission list or PII goes inside. Authorisation is resolved
        # server-side per request.
        token["email"] = user.email
        token["language"] = user.language
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user

        if user.is_suspended:
            raise serializers.ValidationError(
                _("This account is suspended. Please contact your administrator."),
                code="account_suspended",
            )

        data["user"] = UserSerializer(user).data
        data["must_change_password"] = user.must_change_password
        return data

    def get_token_with_session(self, user, *, ip: str | None, user_agent: str):
        """
        Issue a refresh token and record the session it opens.

        Session registration lives here rather than in the view because the
        access token must carry the session's jti, and only the code that
        mints the pair can put a claim inside it. Without that claim,
        revocation cannot be enforced on the access token — which is what made
        "sign out everywhere" ineffective.
        """
        from django.conf import settings

        from . import services

        refresh = self.get_token(user)
        jti = refresh[settings.SIMPLE_JWT.get("JTI_CLAIM", "jti")]

        expires_at = datetime.fromtimestamp(refresh["exp"], tz=UTC)
        services.register_session(
            user, jti=jti, expires_at=expires_at, ip=ip, user_agent=user_agent,
        )

        # Stamp the session onto both halves so the access token can be
        # checked against it on every request.
        refresh["session_jti"] = jti
        access = refresh.access_token
        access["session_jti"] = jti
        return refresh, access


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError(_("The current password is incorrect."))
        return value

    def validate_new_password(self, value):
        validate_password(value, user=self.context["request"].user)
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class LanguageSerializer(serializers.Serializer):
    """Live language switch — no logout required."""

    language = serializers.ChoiceField(choices=["fr", "en"])


class UserSessionSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = UserSession
        fields = [
            "id", "ip_address", "user_agent", "created_at",
            "last_seen_at", "expires_at", "revoked_at", "is_active",
        ]
        read_only_fields = fields
