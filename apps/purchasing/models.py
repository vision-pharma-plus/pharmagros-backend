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
