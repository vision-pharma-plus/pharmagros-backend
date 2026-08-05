"""
Light-touch accounting: what the business spends, and on what.

Deliberately *not* a general ledger. There are no double-entry postings, no
chart of accounts and no trial balance, because a wholesaler who needs those
buys accounting software and files with an accountant. What is missing in that
arrangement is the day-to-day question — where is the money going — and that is
what this module answers, from records the people running the pharmacy already
have to hand.

Money leaving the business has exactly two shapes here:

  * `Expense`      — an operating cost: rent, salaries, utilities, shipping.
  * SupplierPayment — settlement of a supplier's invoice. Lives in
    `apps.purchasing`, next to the invoices it settles, and is *read* by this
    module's reports rather than duplicated into it.

Keeping supplier settlement out of this app is what stops the same payment
being counted twice in a cash-outflow report — there is one row for it, in one
place, and the reports join to it.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import BaseModel


def _localised(instance, base: str) -> str:
    """
    Pick the _fr or _en variant of a paired field for the active language.

    The same helper the catalogue uses, repeated rather than imported to avoid
    a dependency from accounting onto catalog for four lines of logic. French
    is the fallback because it is the column that is always populated.
    """
    suffix = "fr" if (get_language() or "fr").startswith("fr") else "en"
    value = getattr(instance, f"{base}_{suffix}", "")
    return value or getattr(instance, f"{base}_fr", "")


class ExpenseStatus(models.TextChoices):
    """
    Where an expense stands.

    Recorded and paid are separated because they answer different questions: an
    electricity bill entered on the 1st and paid on the 20th is a commitment
    for the whole of that period, and a cash-flow view that treated it as spent
    on the 1st would be wrong about the month's actual outflow.
    """

    DRAFT = "DRAFT", _("Draft")
    RECORDED = "RECORDED", _("Recorded")
    APPROVED = "APPROVED", _("Approved")
    PAID = "PAID", _("Paid")
    CANCELLED = "CANCELLED", _("Cancelled")


class ExpensePaymentMethod(models.TextChoices):
    CASH = "CASH", _("Cash")
    BANK_TRANSFER = "BANK_TRANSFER", _("Bank transfer")
    CHEQUE = "CHEQUE", _("Cheque")
    MOBILE_MONEY = "MOBILE_MONEY", _("Mobile money")
    CARD = "CARD", _("Bank card")
    OTHER = "OTHER", _("Other")


class ExpenseCategory(BaseModel):
    """
    A heading expenses are grouped under for reporting.

    A table rather than a fixed enum: the categories a business wants are the
    ones its own costs fall into, and that list changes. `code` is stable and
    is what the seeded defaults and any report grouping key on, so renaming a
    category for display never breaks the history behind it.
    """

    code = models.CharField(_("code"), max_length=32, unique=True, db_index=True)
    # Paired language columns, as on catalogue categories: a category name is
    # shown to francophone and anglophone staff on the same screen in their own
    # language, so it is data rather than a translatable string in the
    # dictionary. `name` resolves whichever matches the active language.
    name_fr = models.CharField(_("name (French)"), max_length=120)
    name_en = models.CharField(_("name (English)"), max_length=120, blank=True)
    description_fr = models.CharField(_("description (French)"), max_length=255, blank=True)
    description_en = models.CharField(_("description (English)"), max_length=255, blank=True)

    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("expense category")
        verbose_name_plural = _("expense categories")
        ordering = ("name_fr",)

    def __str__(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        return _localised(self, "name")

    @property
    def description(self) -> str:
        return _localised(self, "description")

    @property
    def display_name(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        """
        Fill in the missing language from the one that was supplied.

        Same behaviour as catalogue categories: a user who types a name in one
        language should not have to type it again in the other, and a blank
        column would render as an empty label for half the staff. The machine
        translation is a starting point that can be corrected by editing.
        """
        from apps.core.translation import translate_text

        if self.name_fr and not self.name_en:
            self.name_en = translate_text(self.name_fr, "en")
        elif self.name_en and not self.name_fr:
            self.name_fr = translate_text(self.name_en, "fr")

        if self.description_fr and not self.description_en:
            self.description_en = translate_text(self.description_fr, "en")
        elif self.description_en and not self.description_fr:
            self.description_fr = translate_text(self.description_en, "fr")

        super().save(*args, **kwargs)


class Expense(BaseModel):
    """
    A business cost.

    `expense_date` is when the cost was *incurred*; `paid_date` is when money
    actually left. Both are kept because the cash-outflow report needs the
    second and an expense-by-category report needs the first — collapsing them
    into one field would make one of the two reports lie.

    Amounts are entered tax-inclusive, which is how a receipt from a landlord
    or a utility actually reads. `tax_amount` is recorded separately when it is
    known and recoverable, but it is not derived: assuming VAT on an expense
    that never carried any would overstate the reclaimable amount.
    """

    reference = models.CharField(
        _("reference"), max_length=32, unique=True, db_index=True,
    )
    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.PROTECT, related_name="expenses",
        verbose_name=_("category"),
    )
    status = models.CharField(
        _("status"), max_length=16, choices=ExpenseStatus.choices,
        default=ExpenseStatus.RECORDED, db_index=True,
    )

    description = models.CharField(
        _("description"), max_length=255,
        help_text=_("What this cost was for."),
    )
    # Deliberately roomy and separate from `description`: the brief asks for
    # somewhere to describe an expense properly, and a 255-character summary is
    # not where an explanation of an unusual cost belongs.
    notes = models.TextField(
        _("notes"), blank=True,
        help_text=_("Fuller explanation: what it covered, why it was needed, who authorised it."),
    )

    expense_date = models.DateField(
        _("expense date"), default=timezone.localdate, db_index=True,
        help_text=_("When the cost was incurred."),
    )
    paid_date = models.DateField(
        _("date paid"), null=True, blank=True, db_index=True,
        help_text=_("When money actually left the business."),
    )

    amount = MoneyField(_("amount"))
    tax_amount = MoneyField(_("VAT included"))
    currency = models.CharField(_("currency"), max_length=3, default="BIF")

    payment_method = models.CharField(
        _("payment method"), max_length=20,
        choices=ExpensePaymentMethod.choices, default=ExpensePaymentMethod.CASH,
    )
    payment_reference = models.CharField(
        _("payment reference"), max_length=64, blank=True,
        help_text=_("Cheque number, transfer reference or mobile money code."),
    )

    # Who was paid. A free-text field rather than an FK to Supplier: most
    # overheads go to parties that are not pharmaceutical suppliers — a
    # landlord, the utility company, a mechanic — and forcing them into the
    # supplier master would pollute the list used for purchase orders.
    payee = models.CharField(_("paid to"), max_length=200, blank=True)
    # Set when the cost *does* relate to a supplier, so shipping billed by a
    # supplier can be traced back to them without inventing a supplier record.
    supplier = models.ForeignKey(
        "partners.Supplier", on_delete=models.PROTECT, null=True, blank=True,
        related_name="expenses", verbose_name=_("supplier"),
    )
    # Links a shipping or clearing cost to the consignment it was incurred for.
    purchase_order = models.ForeignKey(
        "purchasing.PurchaseOrder", on_delete=models.PROTECT, null=True, blank=True,
        related_name="expenses", verbose_name=_("related purchase order"),
    )

    receipt_number = models.CharField(_("supplier receipt number"), max_length=64, blank=True)

    recorded_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="recorded_expenses", verbose_name=_("recorded by"),
    )
    approved_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="approved_expenses", verbose_name=_("approved by"),
    )
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("expense")
        verbose_name_plural = _("expenses")
        ordering = ("-expense_date", "-reference")
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0")), name="expense_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(tax_amount__lte=models.F("amount")),
                name="expense_tax_within_amount",
            ),
        ]
        indexes = [
            models.Index(fields=["category", "-expense_date"], name="idx_expense_cat_date"),
            models.Index(fields=["status", "-expense_date"], name="idx_expense_status_date"),
            models.Index(fields=["-paid_date"], name="idx_expense_paid_date"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.description}"

    @property
    def is_editable(self) -> bool:
        """Settled and cancelled costs are history; only the rest may change."""
        return self.status in {ExpenseStatus.DRAFT, ExpenseStatus.RECORDED}

    @property
    def is_paid(self) -> bool:
        return self.status == ExpenseStatus.PAID

    @property
    def net_amount(self) -> Decimal:
        """The cost excluding recoverable VAT — what actually hits the P&L."""
        return self.amount - self.tax_amount


# Seeded by `manage.py seed_expense_categories`. Kept here rather than in a
# data migration so the list is a reviewed artefact under version control, the
# same reasoning as `rbac.PERMISSIONS` and `numbering.DEFAULT_SEQUENCES`.
DEFAULT_EXPENSE_CATEGORIES: list[dict] = [
    {"code": "RENT", "name_fr": "Loyer", "name_en": "Rent"},
    {"code": "SALARIES", "name_fr": "Salaires", "name_en": "Salaries"},
    {"code": "UTILITIES", "name_fr": "Eau et électricité", "name_en": "Utilities"},
    {"code": "SHIPPING", "name_fr": "Transport et fret", "name_en": "Shipping and freight",
     "description_fr": "Fret, dédouanement et livraison des marchandises.",
     "description_en": "Freight, customs clearance and delivery of goods."},
    {"code": "OFFICE_SUPPLIES", "name_fr": "Fournitures de bureau",
     "name_en": "Office supplies"},
    {"code": "MARKETING", "name_fr": "Marketing et publicité", "name_en": "Marketing"},
    {"code": "MAINTENANCE", "name_fr": "Entretien et réparations", "name_en": "Maintenance"},
    {"code": "TRANSPORT", "name_fr": "Carburant et déplacements", "name_en": "Fuel and travel"},
    {"code": "COMMUNICATIONS", "name_fr": "Téléphone et internet",
     "name_en": "Telephone and internet"},
    {"code": "INSURANCE", "name_fr": "Assurances", "name_en": "Insurance"},
    {"code": "TAXES_LICENCES", "name_fr": "Impôts et licences",
     "name_en": "Taxes and licences"},
    {"code": "PROFESSIONAL_FEES", "name_fr": "Honoraires professionnels",
     "name_en": "Professional fees"},
    {"code": "BANK_CHARGES", "name_fr": "Frais bancaires", "name_en": "Bank charges"},
    {"code": "OTHER", "name_fr": "Autres dépenses", "name_en": "Other expenses"},
]
