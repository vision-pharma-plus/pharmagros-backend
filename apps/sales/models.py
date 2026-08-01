"""
Sales documents.

A Sale is the commercial transaction; the Invoice is its fiscal
representation. They are separate models because their lifecycles differ: a
sale can be returned or partially returned long after its invoice was filed,
and a single sale may generate an invoice plus later credit notes.

SaleLineBatch is the traceability record — it captures exactly which batches
were consumed by each line. This is what a recall query walks: batch number →
sale lines → customers.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, PercentField, QuantityField
from apps.core.models import BaseModel


class SaleType(models.TextChoices):
    CASH = "CASH", _("Cash sale")
    CREDIT = "CREDIT", _("Credit sale")


class TenderMethod(models.TextChoices):
    """
    How a cash sale was settled at the counter.

    Deliberately a copy of the tender types in `invoicing.PaymentMethod` rather
    than an import of it: that enum also carries CREDIT_NOTE, which settles an
    invoice by offset and can never be a counter tender. Importing it would
    offer an operator a way to "pay" cash for goods with no money changing
    hands. The overlapping values are identical strings, so the two remain
    directly comparable in reporting.
    """

    CASH = "CASH", _("Cash")
    BANK_TRANSFER = "BANK_TRANSFER", _("Bank transfer")
    CHEQUE = "CHEQUE", _("Cheque")
    MOBILE_MONEY = "MOBILE_MONEY", _("Mobile money")
    CARD = "CARD", _("Bank card")
    OTHER = "OTHER", _("Other")


class SaleStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    CONFIRMED = "CONFIRMED", _("Confirmed")
    DELIVERED = "DELIVERED", _("Delivered")
    COMPLETED = "COMPLETED", _("Completed")
    CANCELLED = "CANCELLED", _("Cancelled")
    RETURNED = "RETURNED", _("Returned")
    PARTIALLY_RETURNED = "PARTIALLY_RETURNED", _("Partially returned")


class Sale(BaseModel):
    """A sales transaction to an institutional customer."""

    sale_number = models.CharField(_("sale number"), max_length=32, unique=True, db_index=True)
    sale_type = models.CharField(
        _("sale type"), max_length=10, choices=SaleType.choices,
        default=SaleType.CASH, db_index=True,
    )
    status = models.CharField(
        _("status"), max_length=20, choices=SaleStatus.choices,
        default=SaleStatus.DRAFT, db_index=True,
    )

    customer = models.ForeignKey(
        "partners.Customer", on_delete=models.PROTECT, related_name="sales",
        verbose_name=_("customer"),
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse", on_delete=models.PROTECT, related_name="sales",
        verbose_name=_("warehouse"),
    )

    sale_date = models.DateTimeField(_("sale date"), default=timezone.now, db_index=True)

    subtotal = MoneyField(_("subtotal"))
    discount_percent = PercentField(_("global discount (%)"))
    discount_amount = MoneyField(_("discount"))
    tax_amount = MoneyField(_("VAT"))
    total_amount = MoneyField(_("total"))
    # Captured at sale time from the batches actually issued, so margin
    # reporting reflects the real cost of the units that shipped rather than
    # a catalogue estimate.
    total_cost = MoneyField(_("total cost of goods sold"))

    salesperson = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="sales", verbose_name=_("salesperson"),
    )
    delivery_address = models.CharField(_("delivery address"), max_length=255, blank=True)
    delivery_note_number = models.CharField(_("delivery note"), max_length=32, blank=True)
    customer_order_reference = models.CharField(
        _("customer order reference"), max_length=64, blank=True,
    )

    # Set when a supervisor authorises a sale that breached the credit limit.
    # Recorded explicitly because an override is a deliberate assumption of
    # risk and must be attributable.
    credit_override_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="credit_overrides", verbose_name=_("credit override authorised by"),
    )
    credit_override_reason = models.CharField(
        _("credit override reason"), max_length=255, blank=True,
    )

    confirmed_at = models.DateTimeField(_("confirmed at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=255, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("sale")
        verbose_name_plural = _("sales")
        ordering = ("-sale_date",)
        constraints = [
            models.CheckConstraint(
                condition=Q(total_amount__gte=Decimal("0")), name="sale_total_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["customer", "-sale_date"], name="idx_sale_customer_date"),
            models.Index(fields=["status", "-sale_date"], name="idx_sale_status_date"),
            models.Index(fields=["-sale_date"], name="idx_sale_date"),
        ]

    def __str__(self) -> str:
        return f"{self.sale_number} — {self.customer.business_name}"

    @property
    def is_editable(self) -> bool:
        return self.status == SaleStatus.DRAFT

    @property
    def gross_margin(self) -> Decimal:
        """Margin on goods sold, excluding VAT (which is not revenue)."""
        return (self.total_amount - self.tax_amount) - self.total_cost

    @property
    def gross_margin_percent(self) -> Decimal:
        net_revenue = self.total_amount - self.tax_amount
        if net_revenue <= 0:
            return Decimal("0")
        return (self.gross_margin / net_revenue) * Decimal("100")


class SaleLine(models.Model):
    """One product line on a sale."""

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="lines", verbose_name=_("sale"),
    )
    line_number = models.PositiveIntegerField(_("line number"), default=1)
    product = models.ForeignKey(
        "catalog.Medicine", on_delete=models.PROTECT, related_name="sale_lines",
        verbose_name=_("product"),
    )

    quantity = QuantityField(_("quantity"))
    quantity_returned = QuantityField(_("quantity returned"))
    unit_price = MoneyField(_("unit price"))
    discount_percent = PercentField(_("discount (%)"))
    discount_amount = MoneyField(_("discount"))
    tax_rate = PercentField(_("VAT rate (%)"))
    tax_amount = MoneyField(_("VAT"))
    line_subtotal = MoneyField(_("subtotal"))
    line_total = MoneyField(_("line total"))
    # Weighted-average landed cost of the batches actually issued.
    unit_cost = MoneyField(_("unit cost"))
    line_cost = MoneyField(_("line cost"))

    class Meta:
        verbose_name = _("sale line")
        verbose_name_plural = _("sale lines")
        ordering = ("sale", "line_number")
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=Decimal("0")), name="sale_line_quantity_positive",
            ),
            models.CheckConstraint(
                condition=Q(quantity_returned__lte=models.F("quantity")),
                name="sale_line_return_within_quantity",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sale.sale_number} L{self.line_number}: {self.product.name}"

    @property
    def quantity_net(self) -> Decimal:
        return self.quantity - self.quantity_returned


class SaleLineBatch(models.Model):
    """
    Which batch supplied which portion of a sale line.

    The traceability backbone. A recall of batch X resolves to the set of
    customers via: SaleLineBatch(batch=X) → SaleLine → Sale → Customer.
    Batch number and expiry are denormalised so the record survives even if
    the batch row is archived.
    """

    sale_line = models.ForeignKey(
        SaleLine, on_delete=models.CASCADE, related_name="batch_allocations",
        verbose_name=_("sale line"),
    )
    batch = models.ForeignKey(
        "inventory.StockBatch", on_delete=models.PROTECT, related_name="sale_allocations",
        verbose_name=_("batch"),
    )
    batch_number = models.CharField(_("batch number"), max_length=64)
    expiry_date = models.DateField(_("expiry date"))
    quantity = QuantityField(_("quantity"))
    quantity_returned = QuantityField(_("quantity returned"))
    unit_cost = MoneyField(_("unit cost"))

    class Meta:
        verbose_name = _("sale line batch allocation")
        verbose_name_plural = _("sale line batch allocations")
        ordering = ("sale_line", "expiry_date")
        indexes = [
            # Powers the recall query.
            models.Index(fields=["batch"], name="idx_slb_batch"),
            models.Index(fields=["batch_number"], name="idx_slb_batch_number"),
        ]

    def __str__(self) -> str:
        return f"{self.batch_number}: {self.quantity}"


class SalesReceipt(BaseModel):
    """
    Proof of payment for a cash sale.

    The primary sales document when the customer settles at the counter. A cash
    sale needs no invoice: the receipt *is* the document handed over, and
    issuing an invoice as well would double-count the sale in every report that
    reads invoices.

    It carries no amounts of its own. Every figure is read from the sale it
    belongs to, which is the single place stock, VAT and revenue were computed.
    Copying totals here would create a second version of the truth that could
    drift from the sale after a return, and reconciling the two would be
    guesswork. `total_amount` and friends are properties for exactly that
    reason.

    A receipt may later acquire an invoice — because the customer asks for one,
    or because tax rules require it. That invoice is generated from this same
    sale and back-references the receipt, so the pair reconciles without any
    second posting. See `sales.services.invoice_for_receipt`.
    """

    receipt_number = models.CharField(
        _("receipt number"), max_length=32, unique=True, db_index=True,
    )
    sale = models.OneToOneField(
        Sale, on_delete=models.PROTECT, related_name="receipt", verbose_name=_("sale"),
    )
    customer = models.ForeignKey(
        "partners.Customer", on_delete=models.PROTECT, related_name="sales_receipts",
        verbose_name=_("customer"),
    )

    # --- Customer identity, frozen at issue -------------------------------
    # Snapshotted for the same reason as on an invoice: the receipt must
    # reproduce who was served as at the date it was issued, even if the
    # customer record is later corrected or rebranded.
    customer_name = models.CharField(_("customer name"), max_length=200)
    customer_nif = models.CharField(_("customer NIF"), max_length=20, blank=True)

    issued_at = models.DateTimeField(_("issued at"), default=timezone.now, db_index=True)
    payment_method = models.CharField(
        _("payment method"), max_length=20,
        choices=TenderMethod.choices, default=TenderMethod.CASH,
    )
    payment_reference = models.CharField(
        _("payment reference"), max_length=64, blank=True,
        help_text=_("Mobile money code, card authorisation or transfer reference."),
    )
    amount_tendered = MoneyField(_("amount tendered"))
    change_given = MoneyField(_("change given"))

    issued_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="issued_receipts", verbose_name=_("issued by"),
    )
    notes = models.TextField(_("notes"), blank=True)

    # Print history, for the same audit reason as on an invoice: every copy of
    # a document given to a customer must be accounted for.
    print_count = models.PositiveIntegerField(_("times printed"), default=0)
    last_printed_at = models.DateTimeField(_("last printed"), null=True, blank=True)

    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("sales receipt")
        verbose_name_plural = _("sales receipts")
        ordering = ("-issued_at",)
        indexes = [
            models.Index(fields=["customer", "-issued_at"], name="idx_receipt_customer_date"),
            models.Index(fields=["-issued_at"], name="idx_receipt_date"),
        ]

    def __str__(self) -> str:
        return f"{self.receipt_number} — {self.customer_name}"

    # --- Amounts, read from the sale --------------------------------------
    # Never stored. See the class docstring.

    @property
    def subtotal(self) -> Decimal:
        return self.sale.subtotal

    @property
    def discount_amount(self) -> Decimal:
        return self.sale.discount_amount

    @property
    def tax_amount(self) -> Decimal:
        return self.sale.tax_amount

    @property
    def total_amount(self) -> Decimal:
        return self.sale.total_amount

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None

    @property
    def invoice(self):
        """
        The invoice raised against this receipt, if one ever was.

        Resolved through the sale rather than stored, so there is exactly one
        link between a sale and its invoice and it cannot disagree with itself.
        """
        return getattr(self.sale, "invoice", None)

    @property
    def has_invoice(self) -> bool:
        return self.invoice is not None


class SaleReturn(BaseModel):
    """
    A customer return.

    Returned pharmaceuticals are only restocked when storage conditions were
    demonstrably maintained; otherwise they are quarantined for disposal.
    `restock` records that decision per line, and it is deliberately not
    defaulted to True — silently returning unverified medicines to sellable
    stock is a patient-safety failure.
    """

    return_number = models.CharField(_("return number"), max_length=32, unique=True, db_index=True)
    sale = models.ForeignKey(
        Sale, on_delete=models.PROTECT, related_name="returns", verbose_name=_("original sale"),
    )
    customer = models.ForeignKey(
        "partners.Customer", on_delete=models.PROTECT, related_name="returns",
        verbose_name=_("customer"),
    )
    return_date = models.DateTimeField(_("return date"), default=timezone.now, db_index=True)
    reason = models.CharField(_("reason"), max_length=255)
    total_amount = MoneyField(_("refunded amount"))
    credit_note = models.ForeignKey(
        "invoicing.Invoice", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sale_returns", verbose_name=_("credit note"),
    )
    processed_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="processed_returns", verbose_name=_("processed by"),
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("sale return")
        verbose_name_plural = _("sale returns")
        ordering = ("-return_date",)

    def __str__(self) -> str:
        return f"{self.return_number} — {self.sale.sale_number}"


class SaleReturnLine(models.Model):
    """One returned line, tied to the batch it came from."""

    sale_return = models.ForeignKey(
        SaleReturn, on_delete=models.CASCADE, related_name="lines", verbose_name=_("return"),
    )
    sale_line = models.ForeignKey(
        SaleLine, on_delete=models.PROTECT, related_name="return_lines",
        verbose_name=_("original line"),
    )
    batch = models.ForeignKey(
        "inventory.StockBatch", on_delete=models.PROTECT, related_name="return_lines",
        verbose_name=_("batch"),
    )
    quantity = QuantityField(_("quantity returned"))
    unit_price = MoneyField(_("unit price"))
    refund_amount = MoneyField(_("refund"))
    restock = models.BooleanField(
        _("return to sellable stock"), default=False,
        help_text=_("Only when storage conditions were verifiably maintained."),
    )
    condition_notes = models.CharField(_("condition on return"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("sale return line")
        verbose_name_plural = _("sale return lines")
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=Decimal("0")), name="return_line_quantity_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sale_return.return_number}: {self.quantity}"
