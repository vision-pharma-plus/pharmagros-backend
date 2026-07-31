from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()


class EmailBackend(ModelBackend):
    """
    Authenticates by email, case-insensitively.

    Runs the password hasher even when no user matches, so response timing
    does not reveal whether an address is registered. Without this, an
    attacker can enumerate valid accounts by measuring latency.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = (username or kwargs.get("email") or "").strip().lower()
        if not email or password is None:
            return None

        try:
            user = UserModel.objects.get(email__iexact=email)
        except UserModel.DoesNotExist:
            UserModel().set_password(password)  # constant-time-ish dummy work
            return None
        except UserModel.MultipleObjectsReturned:  # pragma: no cover - unique constraint
            user = UserModel.objects.filter(email__iexact=email).order_by("pk").first()

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def user_can_authenticate(self, user) -> bool:
        return bool(user.is_active and not user.is_suspended)
