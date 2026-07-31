"""
Invoices, payments and credit/debit notes.

A posted invoice is a fiscal document: once issued to a customer it may not be
edited, only cancelled or corrected by a credit note. That rule is enforced in
`services.py` and reflected here by the status machine — several fields become
read-only the moment `status` leaves DRAFT.

Totals are stored, not computed on read. An invoice must always render exactly
as it did when issued, even if a product's price, VAT rate or name changes
afterwards. Recomputing from the catalogue would silently rewrite history.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, PercentField, QuantityField
from apps.core.models import BaseModel


class InvoiceStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    POSTED = "POSTED", _("Posted")
    PARTIALLY_PAID = "PARTIALLY_PAID", _("Partially paid")
    PAID = "PAID", _("Paid")
    OVERDUE = "OVERDUE", _("Overdue")
    CANCELLED = "CANCELLED", _("Cancelled")


class InvoiceType(models.TextChoices):
    STANDARD = "STANDARD", _("Invoice")
    PROFORMA = "PROFORMA", _("Proforma invoice")
    CREDIT_NOTE = "CREDIT_NOTE", _("Credit note")
    DEBIT_NOTE = "DEBIT_NOTE", _("Debit note")


class PaymentMethod(models.TextChoices):
    CASH = "CASH", _("Cash")
    BANK_TRANSFER = "BANK_TRANSFER", _("Bank transfer")
    CHEQUE = "CHEQUE", _("Cheque")
    MOBILE_MONEY = "MOBILE_MONEY", _("Mobile money")
    CARD = "CARD", _("Bank card")
    CREDIT_NOTE = "CREDIT_NOTE", _("Credit note offset")
    OTHER = "OTHER", _("Other")


# Statuses in which an invoice still owes money and therefore counts toward
# a customer's outstanding balance.
OPEN_STATUSES = (InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE)


class Invoice(BaseModel):
    """A fiscal sales document."""

    invoice_number = models.CharField(
        _("invoice number"), max_length=32, unique=True, db_index=True,
    )
    invoice_type = models.CharField(
        _("document type"), max_length=16,
        choices=InvoiceType.choices, default=InvoiceType.STANDARD, db_index=True,
    )
    status = models.CharField(
        _("status"), max_length=20, choices=InvoiceStatus.choices,
        default=InvoiceStatus.DRAFT, db_index=True,
    )

    customer = models.ForeignKey(
        "partners.Customer", on_delete=models.PROTECT, related_name="invoices",
        verbose_name=_("customer"),
    )
    sale = models.OneToOneField(
        "sales.Sale", on_delete=models.PROTECT, related_name="invoice",
        null=True, blank=True, verbose_name=_("sale"),
    )

    # --- Customer identity, frozen at issue -------------------------------
    # Snapshotted rather than joined: the invoice must reproduce the customer's
    # legal name and NIF *as at the invoice date*. If the customer later
    # rebrands or corrects their NIF, historical invoices must not change —
    # they have already been filed with the tax authority.
    customer_name = models.CharField(_("customer name"), max_length=200)
    customer_nif = models.CharField(_("customer NIF"), max_length=20, blank=True)
    customer_address = models.CharField(_("customer address"), max_length=255, blank=True)
    customer_phone = models.CharField(_("customer telephone"), max_length=20, blank=True)

    invoice_date = models.DateTimeField(_("invoice date"), default=timezone.now, db_index=True)
    due_date = models.DateField(_("due date"), null=True, blank=True, db_index=True)
    payment_terms_days = models.PositiveIntegerField(_("payment terms (days)"), default=0)

    # --- Amounts (all BIF, stored at 4 dp, printed at 0 dp) ---------------
    subtotal = MoneyField(_("subtotal before discount"))
    discount_amount = MoneyField(_("discount"))
    taxable_amount = MoneyField(_("taxable base"))
    tax_amount = MoneyField(_("VAT"))
    total_amount = MoneyField(_("total"))
    paid_amount = MoneyField(_("amount paid"))
    balance_due = MoneyField(_("balance due"), db_index=True)

    currency = models.CharField(_("currency"), max_length=3, default="BIF")

    is_credit_sale = models.BooleanField(_("credit sale"), default=False)
    reference = models.CharField(_("external reference"), max_length=64, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    internal_notes = models.TextField(
        _("internal notes"), blank=True,
        help_text=_("Not printed on the customer-facing document."),
    )

    # --- Lifecycle --------------------------------------------------------
    posted_at = models.DateTimeField(_("posted at"), null=True, blank=True)
    posted_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="posted_invoices", verbose_name=_("posted by"),
    )
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="cancelled_invoices", verbose_name=_("cancelled by"),
    )
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=255, blank=True)

    # Print/email history is a compliance concern: reprints of a fiscal
    # document must be traceable, and a reprint is visually marked as a
    # duplicate so it cannot be passed off as an original.
    print_count = models.PositiveIntegerField(_("times printed"), default=0)
    last_printed_at = models.DateTimeField(_("last printed"), null=True, blank=True)
    emailed_at = models.DateTimeField(_("emailed at"), null=True, blank=True)

    # Set on a credit/debit note to identify the invoice it corrects.
    original_invoice = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="corrections", verbose_name=_("original invoice"),
    )

    class Meta:
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        ordering = ("-invoice_date", "-invoice_number")
        constraints = [
            models.CheckConstraint(
                condition=Q(total_amount__gte=Decimal("0")),
                name="invoice_total_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(paid_amount__gte=Decimal("0")),
                name="invoice_paid_non_negative",
            ),
            # A credit note must point at the invoice it corrects; an
            # unattached credit note cannot be reconciled.
            models.CheckConstraint(
                condition=~Q(invoice_type=InvoiceType.CREDIT_NOTE)
                | Q(original_invoice__isnull=False),
                name="credit_note_requires_original",
            ),
        ]
        indexes = [
            models.Index(fields=["customer", "-invoice_date"], name="idx_invoice_customer_date"),
            models.Index(fields=["status", "due_date"], name="idx_invoice_status_due"),
            models.Index(fields=["-invoice_date"], name="idx_invoice_date"),
        ]

    def __str__(self) -> str:
        return f"{self.invoice_number} — {self.customer_name}"

    @property
    def is_editable(self) -> bool:
        """Only drafts may be modified. Posted documents are fiscal records."""
        return self.status == InvoiceStatus.DRAFT

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.due_date
            and self.balance_due > 0
            and self.status in OPEN_STATUSES
            and self.due_date < timezone.localdate()
        )

    @property
    def days_overdue(self) -> int:
        if not self.is_overdue:
            return 0
        return (timezone.localdate() - self.due_date).days

    @property
    def payment_progress(self) -> Decimal:
        if self.total_amount <= 0:
            return Decimal("0")
        return (self.paid_amount / self.total_amount) * Decimal("100")


class InvoiceLine(models.Model):
    """
    One line of an invoice.

    Product identity is snapshotted (description, code, batch numbers) for the
    same reason as customer identity: the printed document must remain exactly
    reproducible. `batch_numbers` in particular is the traceability record —
    it answers "which lots did this customer receive" during a recall, and
    must survive even if the batch row is later purged.
    """

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="lines", verbose_name=_("invoice"),
    )
    line_number = models.PositiveIntegerField(_("line number"), default=1)

    product = models.ForeignKey(
        "catalog.Medicine", on_delete=models.PROTECT, related_name="invoice_lines",
        null=True, blank=True, verbose_name=_("product"),
    )
    product_code = models.CharField(_("product code"), max_length=32)
    description = models.CharField(_("description"), max_length=255)
    batch_numbers = models.CharField(
        _("batch numbers"), max_length=255, blank=True,
        help_text=_("Comma-separated batches issued for this line."),
    )
    expiry_dates = models.CharField(_("expiry dates"), max_length=255, blank=True)

    quantity = QuantityField(_("quantity"))
    unit_of_measure = models.CharField(_("unit"), max_length=16, blank=True)
    unit_price = MoneyField(_("unit price"))

    discount_percent = PercentField(_("discount (%)"))
    discount_amount = MoneyField(_("discount"))
    tax_rate = PercentField(_("VAT rate (%)"))
    tax_amount = MoneyField(_("VAT"))

    line_subtotal = MoneyField(_("subtotal"))
    line_total = MoneyField(_("line total"))

    # Captured for margin reporting. Never printed on the customer document.
    unit_cost = MoneyField(_("unit cost at sale"))

    class Meta:
        verbose_name = _("invoice line")
        verbose_name_plural = _("invoice lines")
        ordering = ("invoice", "line_number")
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=Decimal("0")), name="invoice_line_quantity_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.invoice.invoice_number} L{self.line_number}: {self.description}"

    @property
    def margin(self) -> Decimal:
        return (self.unit_price - self.unit_cost) * self.quantity


class Payment(BaseModel):
    """
    A customer payment.

    Payments are recorded against the customer and allocated to invoices via
    PaymentAllocation. Modelling it this way — rather than a simple FK to one
    invoice — reflects how wholesale customers actually pay: a single bank
    transfer settling six invoices, or a part payment spread across the
    oldest outstanding balances.
    """

    reference = models.CharField(_("payment reference"), max_length=32, unique=True, db_index=True)
    customer = models.ForeignKey(
        "partners.Customer", on_delete=models.PROTECT, related_name="payments",
        verbose_name=_("customer"),
    )
    payment_date = models.DateTimeField(_("payment date"), default=timezone.now, db_index=True)
    amount = MoneyField(_("amount"))
    allocated_amount = MoneyField(_("allocated amount"))
    method = models.CharField(
        _("payment method"), max_length=20,
        choices=PaymentMethod.choices, default=PaymentMethod.CASH,
    )
    bank_reference = models.CharField(
        _("bank / transaction reference"), max_length=64, blank=True,
        help_text=_("Cheque number, transfer reference or mobile money code."),
    )
    received_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="received_payments", verbose_name=_("received by"),
    )
    notes = models.TextField(_("notes"), blank=True)
    is_reversed = models.BooleanField(_("reversed"), default=False)
    reversal_reason = models.CharField(_("reversal reason"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        ordering = ("-payment_date",)
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0")), name="payment_amount_positive",
            ),
            # Allocating more than was received would create money.
            models.CheckConstraint(
                condition=Q(allocated_amount__lte=models.F("amount")),
                name="payment_allocation_within_amount",
            ),
        ]
        indexes = [models.Index(fields=["customer", "-payment_date"], name="idx_payment_customer")]

    def __str__(self) -> str:
        return f"{self.reference} — {self.amount} BIF"

    @property
    def unallocated_amount(self) -> Decimal:
        return self.amount - self.allocated_amount


class PaymentAllocation(models.Model):
    """Links a payment to an invoice it settles, wholly or partly."""

    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="allocations", verbose_name=_("payment"),
    )
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="payment_allocations",
        verbose_name=_("invoice"),
    )
    amount = MoneyField(_("allocated amount"))
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="payment_allocations",
    )

    class Meta:
        verbose_name = _("payment allocation")
        verbose_name_plural = _("payment allocations")
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0")), name="allocation_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["payment", "invoice"], name="unique_payment_invoice_allocation",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.payment.reference} → {self.invoice.invoice_number}: {self.amount}"
