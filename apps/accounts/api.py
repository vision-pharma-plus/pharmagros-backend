from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core.middleware import client_ip
from apps.core.permissions import HasPermission

from . import services
from .models import Permission, Role, User
from .serializers import (
    LanguageSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PermissionSerializer,
    RoleSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserSessionSerializer,
)


class LoginView(TokenObtainPairView):
    """Obtain a JWT pair. Throttled and lockout-protected."""

    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(tags=["auth"], summary="Log in")
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            email = request.data.get("email") or request.data.get("username", "")
            user = User.objects.filter(email__iexact=email).first()
            if user:
                services.record_login(user, ip=client_ip(request), success=True)
        return response


class RefreshView(TokenRefreshView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(tags=["auth"], summary="Refresh access token")
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"], summary="Log out and blacklist the refresh token", request=None, responses={200: dict})
    def post(self, request):
        """
        Blacklist the presented refresh token.

        Without blacklisting, a "logged out" token remains valid until it
        expires — which on a shared warehouse terminal means the next person
        inherits the session.
        """
        refresh = request.data.get("refresh")
        if refresh:
            try:
                from rest_framework_simplejwt.tokens import RefreshToken

                RefreshToken(refresh).blacklist()
            except Exception:
                # An already-expired or malformed token is not an error worth
                # failing the logout over — the client is discarding it anyway.
                pass

        services.record_login(request.user, ip=client_ip(request), success=True,
                              notes="Logout")
        return Response({"detail": _("Signed out successfully.")})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"], summary="Current user profile", responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    @extend_schema(tags=["auth"], summary="Update own profile", request=UserSerializer, responses=UserSerializer)
    def patch(self, request):
        # Deliberately narrow: a user may edit their own contact details and
        # language, never their roles, active flag or permissions.
        allowed = {"first_name", "last_name", "phone", "language", "job_title"}
        data = {k: v for k, v in request.data.items() if k in allowed}

        serializer = UserSerializer(request.user, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class SetLanguageView(APIView):
    """Persist a language preference; takes effect on the next request."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"], summary="Set preferred language", request=LanguageSerializer, responses={200: LanguageSerializer})
    def post(self, request):
        serializer = LanguageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        request.user.language = serializer.validated_data["language"]
        request.user.save(update_fields=["language", "updated_at"])
        return Response({"language": request.user.language})


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    @extend_schema(tags=["auth"], summary="Change own password", request=PasswordChangeSerializer, responses={200: dict})
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        services.set_password(
            request.user, serializer.validated_data["new_password"], actor=request.user,
        )
        return Response(
            {"detail": _("Password changed. Please sign in again.")}
        )


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"

    @extend_schema(
        tags=["auth"], summary="Request a password reset",
        request=PasswordResetRequestSerializer, responses={200: dict},
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"], is_active=True,
        ).first()

        if user:
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            from .tasks import send_password_reset_email

            send_password_reset_email.delay(str(user.pk), uid, token)

        # Always the same response, whether or not the address exists.
        # Differentiating would turn this endpoint into an account enumerator.
        return Response(
            {
                "detail": _(
                    "If an account exists for that address, a reset link has been sent."
                )
            }
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"

    @extend_schema(
        tags=["auth"], summary="Confirm a password reset",
        request=PasswordResetConfirmSerializer, responses={200: dict},
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            uid = force_str(urlsafe_base64_decode(serializer.validated_data["uid"]))
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            return Response(
                {"error": {"code": "invalid_token", "message": _("This reset link is invalid.")}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, serializer.validated_data["token"]):
            return Response(
                {
                    "error": {
                        "code": "invalid_token",
                        "message": _("This reset link is invalid or has expired."),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        services.set_password(user, serializer.validated_data["new_password"], actor=user)
        return Response({"detail": _("Password reset. You may now sign in.")})


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.prefetch_related("roles").all()
    serializer_class = UserSerializer
    search_fields = ["email", "first_name", "last_name", "employee_code"]
    ordering_fields = ["last_name", "email", "created_at"]
    ordering = ["last_name"]
    filterset_fields = ["is_active", "is_suspended", "roles"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "accounts.view_user",
        "retrieve": "accounts.view_user",
        "create": "accounts.add_user",
        "update": "accounts.change_user",
        "partial_update": "accounts.change_user",
        "destroy": "accounts.deactivate_user",
        "assign_roles": "accounts.assign_roles",
        "reset_password": "accounts.reset_user_password",
        "sessions": "accounts.view_sessions",
        "revoke_sessions": "accounts.revoke_sessions",
        "suspend": "accounts.deactivate_user",
    }

    def get_serializer_class(self):
        return UserCreateSerializer if self.action == "create" else UserSerializer

    def perform_destroy(self, instance):
        """
        Deactivate rather than delete.

        A user row is referenced by every audit entry, stock movement and
        posted document they touched. Deleting it would either break those
        references or orphan the audit trail.
        """
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        services.revoke_all_sessions(instance, reason="Account deactivated")

    @extend_schema(tags=["users"], summary="Assign roles")
    @action(detail=True, methods=["post"], url_path="assign-roles")
    def assign_roles(self, request, pk=None):
        user = self.get_object()
        role_ids = request.data.get("role_ids", [])
        services.assign_roles(user, role_ids, actor=request.user)
        return Response(UserSerializer(user).data)

    @extend_schema(tags=["users"], summary="Reset a user's password (administrator)")
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        new_password = request.data.get("new_password")
        if not new_password:
            return Response(
                {"error": {"code": "password_required", "message": _("A new password is required.")}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Forces a change at next login, so an administrator never retains
        # knowledge of a working credential for another user's account.
        services.set_password(user, new_password, actor=request.user, require_change=True)
        return Response({"detail": _("Password reset. The user must change it at next sign-in.")})

    @extend_schema(tags=["users"], summary="List a user's sessions")
    @action(detail=True, methods=["get"])
    def sessions(self, request, pk=None):
        user = self.get_object()
        return Response(UserSessionSerializer(user.sessions.all(), many=True).data)

    @extend_schema(tags=["users"], summary="Revoke all sessions for a user")
    @action(detail=True, methods=["post"], url_path="revoke-sessions")
    def revoke_sessions(self, request, pk=None):
        user = self.get_object()
        count = services.revoke_all_sessions(
            user, reason=f"Revoked by {request.user.get_username()}"
        )
        return Response({"revoked": count})

    @extend_schema(tags=["users"], summary="Suspend or reinstate a user")
    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        user = self.get_object()
        suspend = bool(request.data.get("suspend", True))
        user.is_suspended = suspend
        user.save(update_fields=["is_suspended", "updated_at"])
        if suspend:
            services.revoke_all_sessions(user, reason="Account suspended")
        return Response(UserSerializer(user).data)


class RoleViewSet(viewsets.ModelViewSet):
    queryset = Role.objects.prefetch_related("permissions").all()
    serializer_class = RoleSerializer
    search_fields = ["code", "name_fr", "name_en"]
    ordering = ["name_fr"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "accounts.view_user",
        "retrieve": "accounts.view_user",
        "create": "accounts.manage_roles",
        "update": "accounts.manage_roles",
        "partial_update": "accounts.manage_roles",
        "destroy": "accounts.manage_roles",
    }

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError

        if instance.is_system:
            # System roles are part of the product's seed contract; deleting
            # one would strand its users and break seed_rbac on next deploy.
            raise ValidationError(
                {"detail": _("System roles cannot be deleted. Deactivate the role instead.")}
            )
        if instance.users.exists():
            raise ValidationError(
                {"detail": _("This role is still assigned to users and cannot be deleted.")}
            )
        instance.delete()


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """The permission catalogue. Read-only: it is seeded from code."""

    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    filterset_fields = ["module", "is_sensitive"]
    ordering = ["module", "code"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "accounts.view_user",
        "retrieve": "accounts.view_user",
    }
