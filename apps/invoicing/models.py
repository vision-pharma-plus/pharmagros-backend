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


class CreditNoteReason(models.TextChoices):
    """
    Why an issued invoice is being corrected.

    Recorded alongside the free-text reason because two of these —
    GOODS_RETURNED and GOODS_DAMAGED — mean stock physically came back and
    must be reconciled, while the rest are purely financial corrections.
    Deriving that from prose would be guesswork.
    """

    WRONG_QUANTITY = "WRONG_QUANTITY", _("Wrong quantity invoiced")
    WRONG_PRICE = "WRONG_PRICE", _("Wrong unit price charged")
    GOODS_RETURNED = "GOODS_RETURNED", _("Goods returned by customer")
    GOODS_DAMAGED = "GOODS_DAMAGED", _("Goods damaged or defective")
    DISCOUNT_GRANTED = "DISCOUNT_GRANTED", _("Discount or rebate granted")
    OTHER = "OTHER", _("Other")


# The reasons that mean physical stock came back and inventory must be
# reconciled, rather than a purely financial correction.
GOODS_RETURN_REASONS = (CreditNoteReason.GOODS_RETURNED, CreditNoteReason.GOODS_DAMAGED)


class FiscalStatus(models.TextChoices):
    """
    Where a document stands with the OBR.

    Deliberately separate from `InvoiceStatus`: commercial state (is it paid?)
    and fiscal state (is it declared?) move independently. An invoice can be
    settled in full while its declaration is still queued behind a network
    outage, and conflating the two would make either question unanswerable.
    """

    NOT_REQUIRED = "NOT_REQUIRED", _("Not declarable")
    PENDING = "PENDING", _("Awaiting declaration")
    DECLARED = "DECLARED", _("Declared to OBR")
    REJECTED = "REJECTED", _("Rejected by OBR")
    CANCELLED = "CANCELLED", _("Cancellation declared")


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
    # Set when this invoice was raised for a sale already settled and receipted
    # at the counter. It is the audit trail for the second document: it says
    # this invoice records the *same* transaction as that receipt rather than a
    # new one, which is what stops the sale being counted twice in revenue.
    # Stock, VAT and accounting entries were all posted when the receipt was
    # issued; nothing is re-posted here.
    source_receipt = models.OneToOneField(
        "sales.SalesReceipt", on_delete=models.PROTECT, related_name="issued_invoice",
        null=True, blank=True, verbose_name=_("source receipt"),
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
    # document must be traceable. The printed page is identical on every copy,
    # so this counter and its audit entries are the whole reprint record.
    print_count = models.PositiveIntegerField(_("times printed"), default=0)
    last_printed_at = models.DateTimeField(_("last printed"), null=True, blank=True)
    emailed_at = models.DateTimeField(_("emailed at"), null=True, blank=True)

    # Set on a credit/debit note to identify the invoice it corrects.
    original_invoice = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="corrections", verbose_name=_("original invoice"),
    )
    # Why the correction was issued. Blank on ordinary invoices.
    credit_reason_code = models.CharField(
        _("correction reason"), max_length=20,
        choices=CreditNoteReason.choices, blank=True, db_index=True,
    )

    # --- OBR declaration --------------------------------------------------
    # The signature is computed locally at posting time and never changes
    # afterwards; the remaining fields are filled in from the OBR's reply
    # once the document has been accepted.
    fiscal_status = models.CharField(
        _("fiscal status"), max_length=16, choices=FiscalStatus.choices,
        default=FiscalStatus.NOT_REQUIRED, db_index=True,
    )
    fiscal_signature = models.CharField(
        _("fiscal signature"), max_length=128, blank=True, db_index=True,
        help_text=_("Identifier under which this document is declared to the OBR."),
    )
    obr_registered_number = models.CharField(
        _("OBR registration number"), max_length=64, blank=True,
    )
    obr_registered_at = models.DateTimeField(
        _("OBR registration date"), null=True, blank=True,
    )
    obr_electronic_signature = models.TextField(
        _("OBR electronic signature"), blank=True,
        help_text=_("Returned by the OBR on acceptance; printed on the customer document."),
    )
    declared_at = models.DateTimeField(_("declared at"), null=True, blank=True)
    declaration_attempts = models.PositiveIntegerField(_("declaration attempts"), default=0)
    last_declaration_error = models.TextField(_("last declaration error"), blank=True)
    # Set when a cancellation has been declared, so a document withdrawn at
    # the OBR is distinguishable from one merely cancelled in our books.
    cancellation_declared_at = models.DateTimeField(
        _("cancellation declared at"), null=True, blank=True,
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
            # The declaration sweep filters on exactly this pair, and it runs
            # often enough that a scan of the whole table would be felt.
            models.Index(
                fields=["fiscal_status", "invoice_date"], name="idx_invoice_fiscal_status"
            ),
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

    @property
    def is_declared(self) -> bool:
        return self.fiscal_status in (FiscalStatus.DECLARED, FiscalStatus.CANCELLED)

    @property
    def needs_declaration(self) -> bool:
        return self.fiscal_status == FiscalStatus.PENDING

    @property
    def declaration_exhausted(self) -> bool:
        """
        True once retrying is no longer worthwhile.

        Read by the sweep to leave a document alone, and by the dashboard to
        surface it for manual attention.
        """
        from django.conf import settings

        return (
            self.fiscal_status == FiscalStatus.REJECTED
            and self.declaration_attempts >= settings.OBR["MAX_ATTEMPTS"]
        )


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


class PaymentReceipt(BaseModel):
    """
    Acknowledgement that an invoice has been settled in full.

    Issued automatically the moment an invoice's balance reaches zero — see
    `services._apply_to_invoice`. A customer who has just paid expects a
    document confirming it, and leaving that to a manual step means the ones
    nobody remembers to issue simply never exist.

    One receipt per invoice, enforced by the OneToOne. An invoice reaches zero
    once: `_apply_to_invoice` only assigns PAID on that transition, and a
    reversal that reopens the invoice cancels the receipt rather than deleting
    it, so the numbered series stays dense and the document that was handed to
    the customer remains on file.

    It carries no amounts of its own, for the same reason `SalesReceipt` does
    not: every figure is read from the invoice it acknowledges, which is the
    single place the total was computed. Copying them here would create a
    second version of the truth that could drift after a credit note.
    """

    receipt_number = models.CharField(
        _("receipt number"), max_length=32, unique=True, db_index=True,
    )
    invoice = models.OneToOneField(
        Invoice, on_delete=models.PROTECT, related_name="payment_receipt",
        verbose_name=_("invoice"),
    )
    customer = models.ForeignKey(
        "partners.Customer", on_delete=models.PROTECT, related_name="payment_receipts",
        verbose_name=_("customer"),
    )

    # Snapshotted at issue, as on every other document handed to a customer:
    # the receipt must reproduce who paid as at the date it was issued, even
    # if the customer record is later corrected or rebranded.
    customer_name = models.CharField(_("customer name"), max_length=200)
    customer_nif = models.CharField(_("customer NIF"), max_length=20, blank=True)

    issued_at = models.DateTimeField(_("issued at"), default=timezone.now, db_index=True)
    issued_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="issued_payment_receipts", verbose_name=_("issued by"),
    )

    # The payment that closed the balance. Kept for the method and bank
    # reference the receipt prints; the full settlement history is the
    # invoice's allocations, since several payments may have contributed.
    settling_payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, null=True, blank=True,
        related_name="receipts_issued", verbose_name=_("settling payment"),
    )

    emailed_at = models.DateTimeField(_("emailed at"), null=True, blank=True)
    print_count = models.PositiveIntegerField(_("times printed"), default=0)
    last_printed_at = models.DateTimeField(_("last printed"), null=True, blank=True)

    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("payment receipt")
        verbose_name_plural = _("payment receipts")
        ordering = ("-issued_at",)
        indexes = [
            models.Index(fields=["customer", "-issued_at"], name="idx_payreceipt_customer"),
            models.Index(fields=["-issued_at"], name="idx_payreceipt_date"),
        ]

    def __str__(self) -> str:
        return f"{self.receipt_number} — {self.customer_name}"

    # --- Amounts, read from the invoice -----------------------------------

    @property
    def amount_paid(self) -> Decimal:
        return self.invoice.paid_amount

    @property
    def invoice_total(self) -> Decimal:
        return self.invoice.total_amount

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None


class FiscalRequestOutcome(models.TextChoices):
    IN_FLIGHT = "IN_FLIGHT", _("In flight")
    SUCCEEDED = "SUCCEEDED", _("Succeeded")
    FAILED = "FAILED", _("Failed")


class FiscalRequestLog(models.Model):
    """
    One exchange with the OBR, recorded verbatim.

    Kept deliberately outside the invoice row. When our records and the
    OBR's disagree about what was filed, this table is the evidence, and it
    has to show every attempt — including the ones that failed and the ones
    that never came back — not merely the final state. Rows are written
    before the request goes out for exactly that reason.

    Not a BaseModel: these are machine-generated transport records, never
    soft-deleted or edited by a user, and there is no actor to stamp.
    """

    id = models.BigAutoField(primary_key=True)
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="fiscal_requests",
        null=True, blank=True, verbose_name=_("invoice"),
    )
    operation = models.CharField(_("operation"), max_length=32, db_index=True)
    endpoint = models.URLField(_("endpoint"), max_length=255)

    request_body = models.JSONField(_("request body"), null=True, blank=True)
    response_body = models.JSONField(_("response body"), null=True, blank=True)
    status_code = models.PositiveSmallIntegerField(_("HTTP status"), null=True, blank=True)
    outcome = models.CharField(
        _("outcome"), max_length=12, choices=FiscalRequestOutcome.choices,
        default=FiscalRequestOutcome.IN_FLIGHT, db_index=True,
    )
    error_message = models.TextField(_("error"), blank=True)

    started_at = models.DateTimeField(_("started at"), auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("OBR request log")
        verbose_name_plural = _("OBR request log")
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["invoice", "-started_at"], name="idx_fiscal_req_invoice"),
            models.Index(fields=["outcome", "-started_at"], name="idx_fiscal_req_outcome"),
        ]

    def __str__(self) -> str:
        return f"{self.operation} {self.outcome} @ {self.started_at:%Y-%m-%d %H:%M:%S}"

    def mark_succeeded(self, status_code: int, body) -> None:
        self.status_code = status_code
        self.response_body = body if isinstance(body, (dict, list)) else {"raw": str(body)[:5000]}
        self.outcome = FiscalRequestOutcome.SUCCEEDED
        self.completed_at = timezone.now()
        self.save(
            update_fields=["status_code", "response_body", "outcome", "completed_at"]
        )

    def mark_failed(self, message: str, *, status_code: int | None = None, body=None) -> None:
        self.status_code = status_code
        if body is not None:
            self.response_body = body if isinstance(body, (dict, list)) else {"raw": str(body)[:5000]}
        self.outcome = FiscalRequestOutcome.FAILED
        self.error_message = (message or "")[:5000]
        self.completed_at = timezone.now()
        self.save(
            update_fields=[
                "status_code", "response_body", "outcome", "error_message", "completed_at",
            ]
        )
