"""
Procurement: requisition → approval → purchase order → goods receipt.

The approval workflow exists for separation of duties. The person who raises
a requisition must not be the person who approves it — that control is what
prevents an employee from ordering goods to an address they control. It is
enforced in `services.approve_order`, not merely by convention.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, PercentField, QuantityField
from apps.core.models import BaseModel


class PurchaseOrderStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    PENDING_APPROVAL = "PENDING_APPROVAL", _("Pending approval")
    APPROVED = "APPROVED", _("Approved")
    REJECTED = "REJECTED", _("Rejected")
    SENT = "SENT", _("Sent to supplier")
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED", _("Partially received")
    RECEIVED = "RECEIVED", _("Fully received")
    CANCELLED = "CANCELLED", _("Cancelled")
    CLOSED = "CLOSED", _("Closed")


# Statuses from which goods may still be booked in.
RECEIVABLE_STATUSES = (
    PurchaseOrderStatus.APPROVED,
    PurchaseOrderStatus.SENT,
    PurchaseOrderStatus.PARTIALLY_RECEIVED,
)


class PurchaseOrder(BaseModel):
    """An order placed on a supplier."""

    order_number = models.CharField(_("order number"), max_length=32, unique=True, db_index=True)
    status = models.CharField(
        _("status"), max_length=20, choices=PurchaseOrderStatus.choices,
        default=PurchaseOrderStatus.DRAFT, db_index=True,
    )

    supplier = models.ForeignKey(
        "partners.Supplier", on_delete=models.PROTECT, related_name="purchase_orders",
        verbose_name=_("supplier"),
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse", on_delete=models.PROTECT, related_name="purchase_orders",
        verbose_name=_("delivery warehouse"),
    )

    order_date = models.DateField(_("order date"), default=timezone.localdate, db_index=True)
    expected_delivery_date = models.DateField(_("expected delivery"), null=True, blank=True)
    actual_delivery_date = models.DateField(_("actual delivery"), null=True, blank=True)

    subtotal = MoneyField(_("subtotal"))
    discount_amount = MoneyField(_("discount"))
    tax_amount = MoneyField(_("VAT"))
    # Import costs are material for a Burundian wholesaler: freight, customs
    # duty and clearing can add 15-30% to landed cost. They are apportioned
    # across received lines so batch cost reflects what the goods really cost.
    freight_cost = MoneyField(_("freight"))
    customs_duty = MoneyField(_("customs duty"))
    other_charges = MoneyField(_("other charges"))
    total_amount = MoneyField(_("total"))

    currency = models.CharField(_("currency"), max_length=3, default="BIF")
    exchange_rate = models.DecimalField(
        _("exchange rate to BIF"), max_digits=18, decimal_places=6, default=Decimal("1"),
        help_text=_("Rate applied when the supplier invoices in foreign currency."),
    )

    payment_terms = models.CharField(_("payment terms"), max_length=10, blank=True)
    supplier_reference = models.CharField(_("supplier reference"), max_length=64, blank=True)
    supplier_invoice_number = models.CharField(
        _("supplier invoice number"), max_length=64, blank=True,
    )
    supplier_invoice_date = models.DateField(_("supplier invoice date"), null=True, blank=True)

    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="requested_orders", verbose_name=_("requested by"),
    )
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    approved_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="approved_orders", verbose_name=_("approved by"),
    )
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)
    rejection_reason = models.CharField(_("rejection reason"), max_length=255, blank=True)
    sent_at = models.DateTimeField(_("sent to supplier at"), null=True, blank=True)
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=255, blank=True)

    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("purchase order")
        verbose_name_plural = _("purchase orders")
        ordering = ("-order_date", "-order_number")
        constraints = [
            models.CheckConstraint(
                condition=Q(total_amount__gte=Decimal("0")), name="po_total_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(exchange_rate__gt=Decimal("0")), name="po_exchange_rate_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["supplier", "-order_date"], name="idx_po_supplier_date"),
            models.Index(fields=["status", "-order_date"], name="idx_po_status_date"),
        ]

    def __str__(self) -> str:
        return f"{self.order_number} — {self.supplier.name}"

    @property
    def is_editable(self) -> bool:
        return self.status in {PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.REJECTED}

    @property
    def can_receive(self) -> bool:
        return self.status in RECEIVABLE_STATUSES

    @property
    def landed_cost_total(self) -> Decimal:
        """Goods value plus all import charges."""
        return self.subtotal + self.freight_cost + self.customs_duty + self.other_charges

    @property
    def is_fully_received(self) -> bool:
        return all(line.is_fully_received for line in self.lines.all())

    @property
    def receipt_progress(self) -> Decimal:
        lines = list(self.lines.all())
        ordered = sum((line.quantity_ordered for line in lines), Decimal("0"))
        received = sum((line.quantity_received for line in lines), Decimal("0"))
        if ordered <= 0:
            return Decimal("0")
        return (received / ordered) * Decimal("100")

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.expected_delivery_date
            and not self.is_fully_received
            and self.status in RECEIVABLE_STATUSES
            and self.expected_delivery_date < timezone.localdate()
        )


class PurchaseOrderLine(models.Model):
    """One ordered product line."""

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="lines",
        verbose_name=_("purchase order"),
    )
    line_number = models.PositiveIntegerField(_("line number"), default=1)
    product = models.ForeignKey(
        "catalog.Medicine", on_delete=models.PROTECT, related_name="purchase_lines",
        verbose_name=_("product"),
    )

    quantity_ordered = QuantityField(_("quantity ordered"))
    quantity_received = QuantityField(_("quantity received"))
    unit_cost = MoneyField(_("unit cost"))
    discount_percent = PercentField(_("discount (%)"))
    discount_amount = MoneyField(_("discount"))
    tax_rate = PercentField(_("VAT rate (%)"))
    tax_amount = MoneyField(_("VAT"))
    line_total = MoneyField(_("line total"))

    expected_expiry_date = models.DateField(
        _("expected expiry"), null=True, blank=True,
        help_text=_("Minimum acceptable expiry date for this line."),
    )
    notes = models.CharField(_("notes"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("purchase order line")
        verbose_name_plural = _("purchase order lines")
        ordering = ("purchase_order", "line_number")
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity_ordered__gt=Decimal("0")), name="po_line_quantity_positive",
            ),
            # Over-receipt beyond the ordered quantity must be a deliberate,
            # separately authorised act — not something a receiving clerk can
            # do by mistyping a number.
            models.CheckConstraint(
                condition=Q(quantity_received__lte=models.F("quantity_ordered")),
                name="po_line_receipt_within_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.purchase_order.order_number} L{self.line_number}: {self.product.name}"

    @property
    def quantity_outstanding(self) -> Decimal:
        return self.quantity_ordered - self.quantity_received

    @property
    def is_fully_received(self) -> bool:
        return self.quantity_received >= self.quantity_ordered


class GoodsReceipt(BaseModel):
    """
    A delivery against a purchase order.

    Separate from the order because partial deliveries are the norm in
    pharmaceutical importing — a single order commonly arrives in two or three
    shipments with different batch numbers and expiry dates.
    """

    receipt_number = models.CharField(
        _("receipt number"), max_length=32, unique=True, db_index=True,
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="receipts",
        verbose_name=_("purchase order"),
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse", on_delete=models.PROTECT, related_name="goods_receipts",
        verbose_name=_("warehouse"),
    )
    receipt_date = models.DateTimeField(_("receipt date"), default=timezone.now, db_index=True)

    delivery_note_number = models.CharField(_("supplier delivery note"), max_length=64, blank=True)
    received_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="goods_receipts", verbose_name=_("received by"),
    )
    # Quality check is a regulatory gate: goods are not sellable until someone
    # qualified confirms packaging integrity, cold chain and documentation.
    quality_checked = models.BooleanField(_("quality checked"), default=False)
    quality_checked_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="quality_checks", verbose_name=_("quality checked by"),
    )
    quality_notes = models.TextField(_("quality notes"), blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("goods receipt")
        verbose_name_plural = _("goods receipts")
        ordering = ("-receipt_date",)

    def __str__(self) -> str:
        return f"{self.receipt_number} — {self.purchase_order.order_number}"


class GoodsReceiptLine(models.Model):
    """
    One received line, carrying the batch identity of the delivered goods.

    Batch number and expiry are captured here, not on the order line, because
    they are only known when the goods physically arrive.
    """

    goods_receipt = models.ForeignKey(
        GoodsReceipt, on_delete=models.CASCADE, related_name="lines", verbose_name=_("receipt"),
    )
    purchase_order_line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines",
        verbose_name=_("order line"),
    )
    product = models.ForeignKey(
        "catalog.Medicine", on_delete=models.PROTECT, related_name="receipt_lines",
        verbose_name=_("product"),
    )
    batch = models.ForeignKey(
        "inventory.StockBatch", on_delete=models.PROTECT, related_name="receipt_lines",
        null=True, blank=True, verbose_name=_("batch created"),
    )

    batch_number = models.CharField(_("batch number"), max_length=64)
    manufacturing_date = models.DateField(_("manufacturing date"), null=True, blank=True)
    expiry_date = models.DateField(_("expiry date"))

    quantity_received = QuantityField(_("quantity received"))
    quantity_rejected = QuantityField(_("quantity rejected"))
    rejection_reason = models.CharField(_("rejection reason"), max_length=255, blank=True)

    unit_cost = MoneyField(_("unit cost"))
    landed_unit_cost = MoneyField(_("landed unit cost"))

    class Meta:
        verbose_name = _("goods receipt line")
        verbose_name_plural = _("goods receipt lines")
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity_received__gt=Decimal("0")),
                name="receipt_line_quantity_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.batch_number}: {self.quantity_received}"


# ---------------------------------------------------------------------------
# Payables: supplier invoices and the payments that settle them
# ---------------------------------------------------------------------------


class SupplierInvoiceStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    AWAITING_PAYMENT = "AWAITING_PAYMENT", _("Awaiting payment")
    PARTIALLY_PAID = "PARTIALLY_PAID", _("Partially paid")
    PAID = "PAID", _("Paid")
    OVERDUE = "OVERDUE", _("Overdue")
    CANCELLED = "CANCELLED", _("Cancelled")


# Statuses in which an invoice still owes money and therefore counts toward a
# supplier's outstanding balance. The mirror of `invoicing.OPEN_STATUSES`.
OPEN_SUPPLIER_INVOICE_STATUSES = (
    SupplierInvoiceStatus.AWAITING_PAYMENT,
    SupplierInvoiceStatus.PARTIALLY_PAID,
    SupplierInvoiceStatus.OVERDUE,
)


class SupplierPaymentMethod(models.TextChoices):
    """
    How money left the business to reach a supplier.

    Deliberately not an import of `invoicing.PaymentMethod`: that enum carries
    CREDIT_NOTE, which settles a *customer* invoice by offset, and offering it
    here would let an operator mark a supplier paid with no money moving. The
    overlapping values are identical strings, so the two remain comparable in
    cash-flow reporting.
    """

    CASH = "CASH", _("Cash")
    BANK_TRANSFER = "BANK_TRANSFER", _("Bank transfer")
    CHEQUE = "CHEQUE", _("Cheque")
    MOBILE_MONEY = "MOBILE_MONEY", _("Mobile money")
    CARD = "CARD", _("Bank card")
    OTHER = "OTHER", _("Other")


class SupplierInvoice(BaseModel):
    """
    A bill received from a supplier — what the business owes, and for what.

    Modelled as its own document rather than as fields on the purchase order
    because the two do not correspond one-to-one in practice: a supplier
    commonly bills several orders on one invoice, bills part of an order after
    a partial shipment, or bills for something with no order at all (freight,
    customs clearance, a storage charge). `purchase_order` is therefore
    nullable, and is a reference for three-way matching rather than the
    invoice's reason for existing.

    Amounts are stored, not derived from the order. The supplier's invoice is
    the authoritative statement of what is owed; where it disagrees with the
    order that disagreement is a fact to be reconciled and must remain
    visible, not something silently overwritten by recomputation.
    """

    invoice_number = models.CharField(
        _("invoice number"), max_length=64, db_index=True,
        help_text=_("The number the supplier put on their document."),
    )
    reference = models.CharField(
        _("internal reference"), max_length=32, unique=True, db_index=True,
        help_text=_("Our own sequential reference for this payable."),
    )
    status = models.CharField(
        _("status"), max_length=20, choices=SupplierInvoiceStatus.choices,
        default=SupplierInvoiceStatus.AWAITING_PAYMENT, db_index=True,
    )

    supplier = models.ForeignKey(
        "partners.Supplier", on_delete=models.PROTECT, related_name="invoices",
        verbose_name=_("supplier"),
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="supplier_invoices",
        null=True, blank=True, verbose_name=_("purchase order"),
        help_text=_("Optional: the order this invoice bills, for three-way matching."),
    )

    invoice_date = models.DateField(_("invoice date"), default=timezone.localdate, db_index=True)
    received_date = models.DateField(_("date received"), null=True, blank=True)
    due_date = models.DateField(_("due date"), null=True, blank=True, db_index=True)

    subtotal = MoneyField(_("subtotal"))
    tax_amount = MoneyField(_("VAT"))
    # Freight and duty appear here as well as on the order because a supplier
    # may bill them on the invoice itself; the order's copy is the estimate,
    # this one is what was actually charged.
    freight_cost = MoneyField(_("freight"))
    customs_duty = MoneyField(_("customs duty"))
    other_charges = MoneyField(_("other charges"))
    total_amount = MoneyField(_("total"))

    # Maintained by `recompute_supplier_invoice_state` from the allocations,
    # never incremented in place. A running tally accumulates error from every
    # reversed payment, and a wrong payable balance is invisible until a
    # supplier disputes their statement.
    paid_amount = MoneyField(_("amount paid"))
    balance_due = MoneyField(_("balance due"), db_index=True)

    currency = models.CharField(_("currency"), max_length=3, default="BIF")
    exchange_rate = models.DecimalField(
        _("exchange rate to BIF"), max_digits=18, decimal_places=6, default=Decimal("1"),
        help_text=_("Rate applied when the supplier invoices in foreign currency."),
    )

    notes = models.TextField(_("notes"), blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("supplier invoice")
        verbose_name_plural = _("supplier invoices")
        ordering = ("-invoice_date", "-reference")
        constraints = [
            models.CheckConstraint(
                condition=Q(total_amount__gte=Decimal("0")),
                name="supplier_invoice_total_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(paid_amount__gte=Decimal("0")),
                name="supplier_invoice_paid_non_negative",
            ),
            # The same supplier cannot send two documents under one number.
            # Scoped to the supplier, not global: two suppliers numbering their
            # invoices "001" is ordinary and must not collide.
            models.UniqueConstraint(
                fields=["supplier", "invoice_number"],
                condition=Q(deleted_at__isnull=True),
                name="unique_supplier_invoice_number",
            ),
        ]
        indexes = [
            models.Index(fields=["supplier", "-invoice_date"], name="idx_supinv_supplier_date"),
            models.Index(fields=["status", "due_date"], name="idx_supinv_status_due"),
        ]

    def __str__(self) -> str:
        return f"{self.invoice_number} — {self.supplier.name}"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_SUPPLIER_INVOICE_STATUSES

    @property
    def is_cancelled(self) -> bool:
        return self.status == SupplierInvoiceStatus.CANCELLED

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.due_date
            and self.balance_due > 0
            and self.is_open
            and self.due_date < timezone.localdate()
        )

    @property
    def days_overdue(self) -> int:
        if not self.is_overdue:
            return 0
        return (timezone.localdate() - self.due_date).days

    @property
    def payment_progress(self) -> Decimal:
        """
        Percentage of the invoice settled, for the progress bar on the UI.

        Clamped to 100: an overpayment is a real condition that shows up in the
        balance, but a bar past full reads as a rendering fault rather than as
        information.
        """
        if self.total_amount <= 0:
            return Decimal("0")
        progress = (self.paid_amount / self.total_amount) * Decimal("100")
        return min(progress, Decimal("100"))


class SupplierPayment(BaseModel):
    """
    Money paid to a supplier.

    Recorded against the supplier and allocated to invoices through
    `SupplierPaymentAllocation`, rather than carrying a single invoice FK. That
    is what makes the two cases the brief calls for both expressible: one
    payment settling several invoices, and one invoice settled by several
    payments over time.

    The mirror of `invoicing.Payment` on the money-out side, and deliberately
    shaped the same way — the reconciliation problem is identical, and a
    reviewer who understands one understands the other.
    """

    reference = models.CharField(
        _("payment reference"), max_length=32, unique=True, db_index=True,
    )
    supplier = models.ForeignKey(
        "partners.Supplier", on_delete=models.PROTECT, related_name="payments",
        verbose_name=_("supplier"),
    )
    payment_date = models.DateField(_("payment date"), default=timezone.localdate, db_index=True)
    amount = MoneyField(_("amount"))
    # Kept in step with the allocations by `_sync_allocated_amount`. Anything
    # unallocated is money paid on account, awaiting an invoice to settle.
    allocated_amount = MoneyField(_("allocated amount"))

    method = models.CharField(
        _("payment method"), max_length=20,
        choices=SupplierPaymentMethod.choices, default=SupplierPaymentMethod.BANK_TRANSFER,
    )
    # Two distinct references, because they answer different questions during a
    # reconciliation: ours identifies the instruction we issued, theirs is what
    # appears on the bank or mobile money statement.
    payment_reference = models.CharField(
        _("payment / transaction number"), max_length=64, blank=True,
        help_text=_("Cheque number or internal transaction number."),
    )
    bank_reference = models.CharField(
        _("bank / mobile money reference"), max_length=64, blank=True,
        help_text=_("Transfer reference or mobile money confirmation code."),
    )
    bank_account = models.CharField(
        _("account debited"), max_length=120, blank=True,
    )

    paid_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="supplier_payments", verbose_name=_("paid by"),
    )
    notes = models.TextField(_("notes"), blank=True)

    # A payment that bounced or was entered in error. Reversed rather than
    # deleted: the money genuinely moved and then came back, and both legs
    # belong in the cash-outflow record.
    is_reversed = models.BooleanField(_("reversed"), default=False)
    reversed_at = models.DateTimeField(_("reversed at"), null=True, blank=True)
    reversal_reason = models.CharField(_("reversal reason"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("supplier payment")
        verbose_name_plural = _("supplier payments")
        ordering = ("-payment_date", "-reference")
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0")), name="supplier_payment_amount_positive",
            ),
            # Allocating more than was paid would settle debt with money that
            # never left the account.
            models.CheckConstraint(
                condition=Q(allocated_amount__lte=models.F("amount")),
                name="supplier_payment_allocation_within_amount",
            ),
        ]
        indexes = [
            models.Index(fields=["supplier", "-payment_date"], name="idx_suppay_supplier_date"),
            models.Index(fields=["-payment_date"], name="idx_suppay_date"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.amount} {self.supplier.currency}"

    @property
    def unallocated_amount(self) -> Decimal:
        return self.amount - self.allocated_amount


class SupplierPaymentAllocation(models.Model):
    """Links a supplier payment to an invoice it settles, wholly or partly."""

    payment = models.ForeignKey(
        SupplierPayment, on_delete=models.CASCADE, related_name="allocations",
        verbose_name=_("payment"),
    )
    supplier_invoice = models.ForeignKey(
        SupplierInvoice, on_delete=models.PROTECT, related_name="payment_allocations",
        verbose_name=_("supplier invoice"),
    )
    amount = MoneyField(_("allocated amount"))
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="supplier_payment_allocations",
    )

    class Meta:
        verbose_name = _("supplier payment allocation")
        verbose_name_plural = _("supplier payment allocations")
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0")),
                name="supplier_allocation_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["payment", "supplier_invoice"],
                name="unique_supplier_payment_invoice_allocation",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.payment.reference} → {self.supplier_invoice.invoice_number}: {self.amount}"
