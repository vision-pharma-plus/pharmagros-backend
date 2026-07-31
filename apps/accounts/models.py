"""
Users, roles and permissions.

RBAC design: a custom Permission/Role pair rather than Django's auth.Group.
Django groups are workable, but this domain needs role metadata Django does
not carry — inheritance between roles, a system-role flag preventing
accidental deletion of "Administrateur Système", and permission grouping by
business module for a usable permission-matrix UI. Django's own permission
framework remains active underneath for the admin site.
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.validators import normalise_phone, validate_burundi_phone


class PermissionModule(models.TextChoices):
    CATALOG = "CATALOG", _("Medicine catalog")
    INVENTORY = "INVENTORY", _("Inventory")
    SALES = "SALES", _("Sales")
    INVOICING = "INVOICING", _("Invoicing")
    PURCHASING = "PURCHASING", _("Purchasing")
    PARTNERS = "PARTNERS", _("Customers & suppliers")
    REPORTING = "REPORTING", _("Reporting")
    ADMINISTRATION = "ADMINISTRATION", _("Administration")
    AUDIT = "AUDIT", _("Audit & compliance")


class Permission(TimeStampedModel):
    """
    A single granular capability, e.g. 'inventory.adjust_stock'.

    Seeded by `manage.py seed_rbac` rather than created ad hoc, so the
    permission surface is a reviewed artefact under version control instead of
    something that drifts per-installation.
    """

    code = models.CharField(_("code"), max_length=100, unique=True, db_index=True)
    name_fr = models.CharField(_("name (French)"), max_length=150)
    name_en = models.CharField(_("name (English)"), max_length=150)
    description_fr = models.TextField(_("description (French)"), blank=True)
    description_en = models.TextField(_("description (English)"), blank=True)
    module = models.CharField(
        _("module"), max_length=32, choices=PermissionModule.choices, db_index=True
    )
    # Marks capabilities that change money, stock or permissions — surfaced
    # distinctly in the permission-matrix UI so granting them is a conscious act.
    is_sensitive = models.BooleanField(_("sensitive"), default=False)

    class Meta:
        verbose_name = _("permission")
        verbose_name_plural = _("permissions")
        ordering = ("module", "code")

    def __str__(self) -> str:
        return self.code

    @property
    def name(self) -> str:
        from django.utils.translation import get_language

        return self.name_fr if (get_language() or "fr").startswith("fr") else self.name_en


class Role(TimeStampedModel):
    """
    A named permission set.

    `inherits_from` gives single-parent inheritance: Pharmacist inherits
    Technician, Store Manager inherits Inventory Officer. Effective
    permissions are the union along the chain, resolved by
    `effective_permissions()` with cycle protection.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(_("code"), max_length=64, unique=True, db_index=True)
    name_fr = models.CharField(_("name (French)"), max_length=100)
    name_en = models.CharField(_("name (English)"), max_length=100)
    description_fr = models.TextField(_("description (French)"), blank=True)
    description_en = models.TextField(_("description (English)"), blank=True)

    permissions = models.ManyToManyField(
        Permission, related_name="roles", blank=True, verbose_name=_("permissions")
    )
    inherits_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="descendants",
        verbose_name=_("inherits from"),
    )

    # System roles ship with the product; deleting one would strand users and
    # break the seed contract, so deletion is blocked at the API layer.
    is_system = models.BooleanField(_("system role"), default=False, editable=False)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("role")
        verbose_name_plural = _("roles")
        ordering = ("name_fr",)

    def __str__(self) -> str:
        return self.name_fr

    @property
    def name(self) -> str:
        from django.utils.translation import get_language

        return self.name_fr if (get_language() or "fr").startswith("fr") else self.name_en

    def effective_permissions(self) -> set[str]:
        """
        Permission codes from this role plus every ancestor.

        Cycle guard: a misconfigured inheritance loop (A -> B -> A) would
        otherwise recurse forever. Data integrity is also enforced on save,
        but defending here keeps a bad row from taking down authorisation.
        """
        codes: set[str] = set()
        seen: set[uuid.UUID] = set()
        node: Role | None = self
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            codes.update(node.permissions.values_list("code", flat=True))
            node = node.inherits_from
        return codes

    def clean(self):
        from django.core.exceptions import ValidationError

        node = self.inherits_from
        seen = {self.pk}
        while node is not None:
            if node.pk in seen:
                raise ValidationError(
                    {"inherits_from": _("Role inheritance cannot contain a cycle.")}
                )
            seen.add(node.pk)
            node = node.inherits_from


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        if extra.get("is_staff") is not True or extra.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """
    Application user.

    Email is the login identifier — staff at a wholesaler reliably have an
    email but not a memorable username, and a single identifier removes a
    class of "which one do I type" support burden.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("email address"), unique=True, db_index=True)
    first_name = models.CharField(_("first name"), max_length=100)
    last_name = models.CharField(_("last name"), max_length=100)
    phone = models.CharField(
        _("telephone"), max_length=20, blank=True, validators=[validate_burundi_phone]
    )
    employee_code = models.CharField(_("employee code"), max_length=32, blank=True)
    job_title = models.CharField(_("job title"), max_length=100, blank=True)

    roles = models.ManyToManyField(Role, related_name="users", blank=True, verbose_name=_("roles"))

    language = models.CharField(
        _("preferred language"),
        max_length=5,
        choices=[("fr", "Français"), ("en", "English")],
        default="fr",
    )

    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(_("admin site access"), default=False)
    # Distinct from is_active: suspension is a reversible administrative
    # action that should read differently in the audit trail than
    # deactivating a departed employee.
    is_suspended = models.BooleanField(_("suspended"), default=False)

    mfa_enabled = models.BooleanField(_("MFA enabled"), default=False)
    must_change_password = models.BooleanField(_("must change password"), default=False)
    password_changed_at = models.DateTimeField(_("password changed at"), null=True, blank=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ("last_name", "first_name")
        indexes = [models.Index(fields=["is_active", "is_suspended"])]

    def __str__(self) -> str:
        return f"{self.get_full_name()} <{self.email}>"

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        if self.phone:
            self.phone = normalise_phone(self.phone)
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        return self.first_name

    # -- RBAC ---------------------------------------------------------------
    @property
    def primary_role_name(self) -> str:
        role = self.roles.filter(is_active=True).first()
        return role.code if role else ""

    def effective_permissions(self) -> set[str]:
        """
        Union of permissions across all active roles, including inheritance.

        Cached per instance for the request's lifetime: authorisation is
        checked many times per request, and re-walking the role graph each
        time would add avoidable queries to every endpoint.
        """
        if not hasattr(self, "_perm_cache"):
            codes: set[str] = set()
            for role in self.roles.filter(is_active=True).prefetch_related("permissions"):
                codes |= role.effective_permissions()
            self._perm_cache = codes
        return self._perm_cache

    def has_perm_code(self, code: str) -> bool:
        if self.is_superuser:
            return True
        if not self.is_active or self.is_suspended:
            return False
        return code in self.effective_permissions()

    def has_any_perm(self, *codes: str) -> bool:
        return any(self.has_perm_code(c) for c in codes)

    def invalidate_permission_cache(self) -> None:
        if hasattr(self, "_perm_cache"):
            del self._perm_cache


class PasswordHistory(models.Model):
    """
    Previous password hashes, for the reuse policy.

    Stores hashes only — never plaintext, never reversible. Retention is
    bounded by the validator's history_length; older rows are pruned on write.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_history")
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("password history entry")
        verbose_name_plural = _("password history")


class UserSession(models.Model):
    """
    Active refresh-token sessions.

    Enables "sign out everywhere" and gives administrators visibility of
    concurrent sessions — a requirement when several staff share a terminal
    on a warehouse floor.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    jti = models.CharField(max_length=64, unique=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-last_seen_at",)
        verbose_name = _("user session")
        verbose_name_plural = _("user sessions")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()
