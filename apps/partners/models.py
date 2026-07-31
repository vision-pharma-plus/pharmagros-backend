"""
Trading partners: customers (pharmacies, hospitals, clinics, NGOs) and
suppliers.

Customer credit is the commercially dangerous part of a wholesale business —
most of the balance sheet is receivables. The model therefore carries an
explicit credit limit, a maintained outstanding balance, and payment terms,
with the balance recomputed from invoices rather than trusted as a running
tally (see services.recompute_balance).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import BaseModel
from apps.core.validators import (
    normalise_nif,
    normalise_phone,
    validate_burundi_phone,
    validate_nif,
)


class CustomerType(models.TextChoices):
    PHARMACY = "PHARMACY", _("Pharmacy")
    HOSPITAL = "HOSPITAL", _("Hospital")
    CLINIC = "CLINIC", _("Clinic")
    HEALTH_CENTRE = "HEALTH_CENTRE", _("Health centre")
    NGO = "NGO", _("NGO / humanitarian organisation")
    GOVERNMENT = "GOVERNMENT", _("Government / public sector")
    WHOLESALER = "WHOLESALER", _("Other wholesaler")
    OTHER = "OTHER", _("Other")


class PaymentTerms(models.TextChoices):
    CASH = "CASH", _("Cash on delivery")
    NET_7 = "NET_7", _("7 days")
    NET_15 = "NET_15", _("15 days")
    NET_30 = "NET_30", _("30 days")
    NET_45 = "NET_45", _("45 days")
    NET_60 = "NET_60", _("60 days")
    NET_90 = "NET_90", _("90 days")


# Days each term implies. Kept as data so due-date arithmetic has one home.
PAYMENT_TERM_DAYS = {
    PaymentTerms.CASH: 0,
    PaymentTerms.NET_7: 7,
    PaymentTerms.NET_15: 15,
    PaymentTerms.NET_30: 30,
    PaymentTerms.NET_45: 45,
    PaymentTerms.NET_60: 60,
    PaymentTerms.NET_90: 90,
}


class PartnerStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    INACTIVE = "INACTIVE", _("Inactive")
    BLOCKED = "BLOCKED", _("Blocked")
    PENDING = "PENDING", _("Pending approval")


class Customer(BaseModel):
    """An institutional buyer."""

    customer_code = models.CharField(_("customer code"), max_length=32, unique=True, db_index=True)
    business_name = models.CharField(_("business name"), max_length=200, db_index=True)
    trading_name = models.CharField(_("trading name"), max_length=200, blank=True)
    customer_type = models.CharField(
        _("customer type"), max_length=20,
        choices=CustomerType.choices, default=CustomerType.PHARMACY,
    )

    # NIF is optional at the model level so a cash walk-in can be recorded,
    # but the sales service requires it for credit sales and above the OBR
    # identification threshold.
    nif = models.CharField(
        _("NIF"), max_length=20, blank=True, db_index=True, validators=[validate_nif],
        help_text=_("Tax identification number (Numéro d'Identification Fiscale)."),
    )
    rc_number = models.CharField(_("trade register number"), max_length=40, blank=True)
    pharmacy_licence = models.CharField(
        _("pharmacy licence number"), max_length=64, blank=True,
        help_text=_("Operating licence issued by the Ministry of Health."),
    )
    licence_expiry = models.DateField(_("licence expiry"), null=True, blank=True)

    contact_person = models.CharField(_("contact person"), max_length=150, blank=True)
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(
        _("telephone"), max_length=20, blank=True, validators=[validate_burundi_phone],
    )
    alternate_phone = models.CharField(_("alternate telephone"), max_length=20, blank=True)

    address = models.CharField(_("address"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=80, default="Bujumbura", db_index=True)
    province = models.CharField(_("province"), max_length=80, blank=True)
    country = models.CharField(_("country"), max_length=80, default="Burundi")

    # --- Credit control ---------------------------------------------------
    credit_limit = MoneyField(
        _("credit limit"), validators=[MinValueValidator(Decimal("0"))],
        help_text=_("Maximum outstanding balance permitted. Zero means cash only."),
    )
    # Maintained by services.recompute_balance from posted invoices and
    # payments — never incremented ad hoc, because a drifting balance would
    # silently disable credit control.
    outstanding_balance = MoneyField(_("outstanding balance"))
    payment_terms = models.CharField(
        _("payment terms"), max_length=10,
        choices=PaymentTerms.choices, default=PaymentTerms.CASH,
    )
    credit_blocked = models.BooleanField(
        _("credit blocked"), default=False,
        help_text=_("Blocks all credit sales regardless of the available limit."),
    )
    credit_block_reason = models.CharField(_("reason for credit block"), max_length=255, blank=True)

    discount_percent = models.DecimalField(
        _("standard discount (%)"), max_digits=5, decimal_places=2, default=Decimal("0"),
    )

    status = models.CharField(
        _("status"), max_length=16, choices=PartnerStatus.choices,
        default=PartnerStatus.ACTIVE, db_index=True,
    )
    notes = models.TextField(_("notes"), blank=True)
    first_sale_at = models.DateTimeField(_("first sale"), null=True, blank=True)
    last_sale_at = models.DateTimeField(_("last sale"), null=True, blank=True)

    class Meta:
        verbose_name = _("customer")
        verbose_name_plural = _("customers")
        ordering = ("business_name",)
        constraints = [
            models.CheckConstraint(
                condition=Q(credit_limit__gte=Decimal("0")),
                name="customer_credit_limit_non_negative",
            ),
            # A NIF must be unique among active customers when supplied;
            # two live accounts sharing a tax ID means duplicated receivables.
            models.UniqueConstraint(
                fields=["nif"],
                condition=Q(deleted_at__isnull=True) & ~Q(nif=""),
                name="unique_active_customer_nif",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "business_name"], name="idx_customer_status_name"),
            models.Index(fields=["customer_type", "status"], name="idx_customer_type"),
        ]

    def __str__(self) -> str:
        return f"{self.customer_code} — {self.business_name}"

    def save(self, *args, **kwargs):
        if self.nif:
            self.nif = normalise_nif(self.nif)
        if self.phone:
            self.phone = normalise_phone(self.phone)
        if self.alternate_phone:
            self.alternate_phone = normalise_phone(self.alternate_phone)
        super().save(*args, **kwargs)

    # -- credit ------------------------------------------------------------
    @property
    def available_credit(self) -> Decimal:
        return max(Decimal("0"), self.credit_limit - self.outstanding_balance)

    @property
    def credit_utilisation(self) -> Decimal:
        """Percentage of the limit consumed; 0 when the customer is cash-only."""
        if self.credit_limit <= 0:
            return Decimal("0")
        return (self.outstanding_balance / self.credit_limit) * Decimal("100")

    @property
    def is_over_limit(self) -> bool:
        return self.credit_limit > 0 and self.outstanding_balance > self.credit_limit

    @property
    def is_institutional(self) -> bool:
        """Eligible for wholesale pricing."""
        return self.customer_type in {
            CustomerType.HOSPITAL, CustomerType.CLINIC, CustomerType.HEALTH_CENTRE,
            CustomerType.NGO, CustomerType.GOVERNMENT, CustomerType.WHOLESALER,
        }

    @property
    def payment_term_days(self) -> int:
        return PAYMENT_TERM_DAYS.get(self.payment_terms, 0)

    @property
    def licence_is_expired(self) -> bool:
        """
        Whether the operating licence has lapsed.

        Supplying a pharmacy whose licence has expired exposes the wholesaler
        to regulatory action, so this is surfaced at the point of sale.
        """
        return bool(self.licence_expiry and self.licence_expiry < timezone.localdate())

    def can_buy_on_credit(self, amount: Decimal) -> tuple[bool, str]:
        """
        Whether a credit sale of `amount` is permissible.

        Returns (allowed, reason) rather than raising, so the caller can decide
        whether to block or to request an override from a supervisor.
        """
        if self.status != PartnerStatus.ACTIVE:
            return False, str(_("The customer account is not active."))
        if self.credit_blocked:
            return False, str(
                _("Credit is blocked for this customer: %(reason)s")
                % {"reason": self.credit_block_reason or _("no reason recorded")}
            )
        if self.payment_terms == PaymentTerms.CASH:
            return False, str(_("This customer is configured for cash payment only."))
        if self.credit_limit <= 0:
            return False, str(_("No credit limit has been set for this customer."))
        if self.outstanding_balance + amount > self.credit_limit:
            return False, str(
                _("Credit limit exceeded: balance %(balance)s + %(amount)s exceeds the limit of %(limit)s BIF.")
                % {
                    "balance": self.outstanding_balance,
                    "amount": amount,
                    "limit": self.credit_limit,
                }
            )
        return True, ""


class Supplier(BaseModel):
    """A pharmaceutical supplier, typically an importer or foreign manufacturer."""

    supplier_code = models.CharField(_("supplier code"), max_length=32, unique=True, db_index=True)
    name = models.CharField(_("supplier name"), max_length=200, db_index=True)
    nif = models.CharField(_("NIF"), max_length=20, blank=True, db_index=True)

    contact_person = models.CharField(_("contact person"), max_length=150, blank=True)
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("telephone"), max_length=32, blank=True)
    address = models.CharField(_("address"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=80, blank=True)
    # Not defaulted to Burundi: most pharmaceutical supply is imported, and a
    # wrong default would quietly corrupt customs and origin reporting.
    country = models.CharField(_("country"), max_length=80, blank=True)

    payment_terms = models.CharField(
        _("payment terms"), max_length=10,
        choices=PaymentTerms.choices, default=PaymentTerms.NET_30,
    )
    currency = models.CharField(
        _("invoicing currency"), max_length=3, default="BIF",
        help_text=_("Currency in which this supplier invoices (BIF, USD, EUR)."),
    )
    lead_time_days = models.PositiveIntegerField(
        _("average lead time (days)"), default=30,
        help_text=_("Used to compute reorder timing."),
    )

    bank_name = models.CharField(_("bank"), max_length=120, blank=True)
    bank_account = models.CharField(_("bank account"), max_length=64, blank=True)
    swift_code = models.CharField(_("SWIFT / BIC"), max_length=16, blank=True)

    status = models.CharField(
        _("status"), max_length=16, choices=PartnerStatus.choices,
        default=PartnerStatus.ACTIVE, db_index=True,
    )
    is_approved = models.BooleanField(
        _("approved supplier"), default=False,
        help_text=_("Only approved suppliers may be selected on a purchase order."),
    )
    approval_notes = models.TextField(_("approval notes"), blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("supplier")
        verbose_name_plural = _("suppliers")
        ordering = ("name",)
        indexes = [models.Index(fields=["status", "name"], name="idx_supplier_status_name")]

    def __str__(self) -> str:
        return f"{self.supplier_code} — {self.name}"

    def save(self, *args, **kwargs):
        if self.nif:
            self.nif = normalise_nif(self.nif)
        super().save(*args, **kwargs)

    @property
    def payment_term_days(self) -> int:
        return PAYMENT_TERM_DAYS.get(self.payment_terms, 30)


class CustomerContact(BaseModel):
    """Additional contacts — hospitals in particular have several."""

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="contacts", verbose_name=_("customer"),
    )
    name = models.CharField(_("name"), max_length=150)
    role = models.CharField(_("role"), max_length=100, blank=True)
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("telephone"), max_length=20, blank=True)
    is_primary = models.BooleanField(_("primary contact"), default=False)

    class Meta:
        verbose_name = _("customer contact")
        verbose_name_plural = _("customer contacts")
        ordering = ("-is_primary", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.customer.business_name})"


class CreditLimitChange(models.Model):
    """
    Immutable log of credit limit changes.

    Raising a customer's credit limit is a material financial decision. This
    history is what lets an auditor ask "who authorised this exposure, and
    when" — a question that arises whenever a receivable goes bad.
    """

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="credit_limit_history",
    )
    old_limit = MoneyField(_("previous limit"))
    new_limit = MoneyField(_("new limit"))
    reason = models.CharField(_("reason"), max_length=255)
    changed_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="credit_limit_changes",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("credit limit change")
        verbose_name_plural = _("credit limit changes")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.customer.customer_code}: {self.old_limit} → {self.new_limit}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Credit limit history entries are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Credit limit history entries cannot be deleted.")
