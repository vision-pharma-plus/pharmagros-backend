"""
Purchasing services.

Two controls carry most of the weight here:

  * Separation of duties — the requester cannot approve their own order.
  * Landed cost apportionment — freight, duty and clearing are spread across
    received lines by value, so batch cost reflects the true cost of goods.
    Ignoring these charges understates COGS and overstates margin, which is
    how a wholesaler convinces itself it is profitable when it is not.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record, snapshot
from apps.core.exceptions import BusinessRuleViolation, DocumentLocked, InvalidStateTransition
from apps.core.models import AuditAction
from apps.core.money import compute_line, q_internal, sum_money
from apps.core.numbering import next_number

from .models import (
    OPEN_SUPPLIER_INVOICE_STATUSES,
    RECEIVABLE_STATUSES,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierPayment,
    SupplierPaymentAllocation,
    SupplierPaymentMethod,
)

ZERO = Decimal("0")


def _write_lines(order: PurchaseOrder, lines: list[dict]) -> None:
    """
    Replace an order's lines with the supplied set.

    Shared by create and update so an edited draft is costed by exactly the
    same rules as a new order — a second implementation would eventually
    disagree with this one about discount or VAT, and the divergence would
    surface as an order whose total changed when it was edited.
    """
    order.lines.all().delete()

    for index, data in enumerate(lines, start=1):
        product = data["product"]
        quantity = q_internal(data["quantity_ordered"])
        unit_cost = q_internal(data["unit_cost"])
        discount = q_internal(data.get("discount_percent", ZERO))
        tax_rate = data.get("tax_rate")
        tax_rate = q_internal(
            tax_rate if tax_rate is not None else product.effective_vat_rate
        )

        if quantity <= ZERO:
            raise BusinessRuleViolation(
                _("Ordered quantities must be greater than zero."), code="invalid_quantity"
            )

        amounts = compute_line(quantity, unit_cost, discount, tax_rate)
        PurchaseOrderLine.objects.create(
            purchase_order=order,
            line_number=index,
            product=product,
            quantity_ordered=quantity,
            unit_cost=unit_cost,
            discount_percent=discount,
            discount_amount=amounts["discount"],
            tax_rate=tax_rate,
            tax_amount=amounts["tax"],
            line_total=amounts["total"],
            expected_expiry_date=data.get("expected_expiry_date"),
            notes=data.get("notes", ""),
        )


def _recalculate_order(order: PurchaseOrder) -> PurchaseOrder:
    lines = list(order.lines.all())
    order.subtotal = sum_money(line.line_total - line.tax_amount for line in lines)
    order.discount_amount = sum_money(line.discount_amount for line in lines)
    order.tax_amount = sum_money(line.tax_amount for line in lines)
    order.total_amount = q_internal(
        sum_money(line.line_total for line in lines)
        + order.freight_cost
        + order.customs_duty
        + order.other_charges
    )
    return order


@transaction.atomic
def create_order(
    *,
    supplier,
    warehouse,
    lines: list[dict],
    expected_delivery_date=None,
    freight_cost: Decimal = ZERO,
    customs_duty: Decimal = ZERO,
    other_charges: Decimal = ZERO,
    currency: str = "BIF",
    exchange_rate: Decimal = Decimal("1"),
    supplier_reference: str = "",
    notes: str = "",
    actor=None,
) -> PurchaseOrder:
    """
    Create a DRAFT purchase order.

    Only approved suppliers may be ordered from. Sourcing pharmaceuticals from
    an unvetted supplier is both a quality risk and a regulatory finding, so
    it is blocked at creation rather than caught at receipt.
    """
    if not lines:
        raise BusinessRuleViolation(
            _("A purchase order must contain at least one line."), code="empty_order"
        )
    if not supplier.is_approved:
        raise BusinessRuleViolation(
            _("%(name)s is not an approved supplier. Approve the supplier before ordering.")
            % {"name": supplier.name},
            code="supplier_not_approved",
        )

    order = PurchaseOrder.objects.create(
        order_number=next_number("purchasing.order"),
        supplier=supplier,
        warehouse=warehouse,
        expected_delivery_date=(
            expected_delivery_date
            or timezone.localdate() + timezone.timedelta(days=supplier.lead_time_days)
        ),
        freight_cost=q_internal(freight_cost),
        customs_duty=q_internal(customs_duty),
        other_charges=q_internal(other_charges),
        currency=currency,
        exchange_rate=exchange_rate,
        payment_terms=supplier.payment_terms,
        supplier_reference=supplier_reference,
        requested_by=actor,
        notes=notes,
        created_by=actor,
    )

    _write_lines(order, lines)

    _recalculate_order(order)
    order.save()

    record(
        AuditAction.CREATE,
        PurchaseOrder._meta.label,
        entity_id=str(order.pk),
        entity_label=order.order_number,
        new_value=snapshot(order),
        actor=actor,
    )
    return order


@transaction.atomic
def update_order(
    order: PurchaseOrder,
    *,
    supplier=None,
    warehouse=None,
    lines: list[dict] | None = None,
    expected_delivery_date=None,
    freight_cost: Decimal | None = None,
    customs_duty: Decimal | None = None,
    other_charges: Decimal | None = None,
    currency: str | None = None,
    exchange_rate: Decimal | None = None,
    supplier_reference: str | None = None,
    notes: str | None = None,
    actor=None,
) -> PurchaseOrder:
    """
    Amend a draft order.

    Only DRAFT and REJECTED orders may be edited. Once an order is approved it
    is an authorisation someone put their name to, and a system that let the
    requester quietly change the quantities afterwards would make that
    approval worthless — which is the same reason `approve_order` refuses
    self-approval. A rejected order is editable precisely so it can be
    corrected and resubmitted.
    """
    if not order.is_editable:
        raise DocumentLocked(
            _("A %(status)s order cannot be edited. Cancel it and raise a new one.")
            % {"status": order.get_status_display()},
            details={"status": order.status},
        )

    before = snapshot(order)

    if supplier is not None:
        # Re-checked on every edit, not only at creation: a supplier can lose
        # approval between raising a draft and submitting it, and the whole
        # point of the control is that unvetted stock is never ordered.
        if not supplier.is_approved:
            raise BusinessRuleViolation(
                _("%(name)s is not an approved supplier. Approve the supplier before ordering.")
                % {"name": supplier.name},
                code="supplier_not_approved",
            )
        order.supplier = supplier
        order.payment_terms = supplier.payment_terms

    if warehouse is not None:
        order.warehouse = warehouse
    if expected_delivery_date is not None:
        order.expected_delivery_date = expected_delivery_date
    if freight_cost is not None:
        order.freight_cost = q_internal(freight_cost)
    if customs_duty is not None:
        order.customs_duty = q_internal(customs_duty)
    if other_charges is not None:
        order.other_charges = q_internal(other_charges)
    if currency is not None:
        order.currency = currency
    if exchange_rate is not None:
        order.exchange_rate = exchange_rate
    if supplier_reference is not None:
        order.supplier_reference = supplier_reference
    if notes is not None:
        order.notes = notes

    if lines is not None:
        if not lines:
            raise BusinessRuleViolation(
                _("A purchase order must contain at least one line."), code="empty_order"
            )
        _write_lines(order, lines)

    order.updated_by = actor
    _recalculate_order(order)
    order.save()

    record(
        AuditAction.UPDATE,
        PurchaseOrder._meta.label,
        entity_id=str(order.pk),
        entity_label=order.order_number,
        previous_value=before,
        new_value=snapshot(order),
        actor=actor,
    )
    return order


@transaction.atomic
def submit_for_approval(order: PurchaseOrder, *, actor=None) -> PurchaseOrder:
    if order.status not in {PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.REJECTED}:
        raise InvalidStateTransition(
            _("Only a draft or rejected order can be submitted for approval."),
            details={"status": order.status},
        )
    if not order.lines.exists():
        raise BusinessRuleViolation(
            _("An order cannot be submitted without lines."), code="empty_order"
        )

    order.status = PurchaseOrderStatus.PENDING_APPROVAL
    order.submitted_at = timezone.now()
    order.rejection_reason = ""
    order.updated_by = actor
    order.save(
        update_fields=["status", "submitted_at", "rejection_reason", "updated_by", "updated_at"]
    )

    from apps.notifications.services import notify_permission_holders

    notify_permission_holders(
        permission_code="purchasing.approve_order",
        code="PO_APPROVAL_REQUEST",
        title=str(_("Purchase order awaiting approval")),
        body=str(
            _("Order %(number)s for %(supplier)s (%(amount)s BIF) requires approval.")
            % {
                "number": order.order_number,
                "supplier": order.supplier.name,
                "amount": order.total_amount,
            }
        ),
        link=f"/purchasing/orders/{order.pk}",
    )

    record(
        AuditAction.UPDATE,
        PurchaseOrder._meta.label,
        entity_id=str(order.pk),
        entity_label=order.order_number,
        new_value={"status": order.status},
        changed_fields=["status"],
        notes="Submitted for approval",
        actor=actor,
    )
    return order


@transaction.atomic
def approve_order(order: PurchaseOrder, *, actor, notes: str = "") -> PurchaseOrder:
    """
    Approve a purchase order.

    Separation of duties: the requester may not approve their own order. This
    is the control that prevents a single employee from both creating and
    authorising a purchase — the classic procurement fraud pattern.
    """
    if order.status != PurchaseOrderStatus.PENDING_APPROVAL:
        raise InvalidStateTransition(
            _("Only an order pending approval can be approved."),
            details={"status": order.status},
        )
    if actor is None:
        raise BusinessRuleViolation(
            _("An approving user must be identified."), code="approver_required"
        )
    if order.requested_by_id and order.requested_by_id == actor.pk:
        raise BusinessRuleViolation(
            _("You cannot approve a purchase order that you raised yourself. "
              "Approval must come from a different user."),
            code="separation_of_duties",
            details={"requested_by": str(order.requested_by_id)},
        )

    order.status = PurchaseOrderStatus.APPROVED
    order.approved_by = actor
    order.approved_at = timezone.now()
    order.updated_by = actor
    order.save(
        update_fields=["status", "approved_by", "approved_at", "updated_by", "updated_at"]
    )

    record(
        AuditAction.APPROVE,
        PurchaseOrder._meta.label,
        entity_id=str(order.pk),
        entity_label=order.order_number,
        new_value={
            "status": order.status,
            "approved_by": actor.get_username(),
            "total_amount": str(order.total_amount),
        },
        changed_fields=["status"],
        notes=f"Purchase order approved. {notes}".strip(),
        actor=actor,
    )
    return order


@transaction.atomic
def reject_order(order: PurchaseOrder, *, reason: str, actor=None) -> PurchaseOrder:
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to reject an order."), code="reason_required"
        )
    if order.status != PurchaseOrderStatus.PENDING_APPROVAL:
        raise InvalidStateTransition(
            _("Only an order pending approval can be rejected."),
            details={"status": order.status},
        )

    order.status = PurchaseOrderStatus.REJECTED
    order.rejection_reason = reason
    order.updated_by = actor
    order.save(update_fields=["status", "rejection_reason", "updated_by", "updated_at"])

    record(
        AuditAction.REJECT,
        PurchaseOrder._meta.label,
        entity_id=str(order.pk),
        entity_label=order.order_number,
        new_value={"status": order.status},
        changed_fields=["status"],
        notes=f"Purchase order rejected: {reason}",
        actor=actor,
    )
    return order


@transaction.atomic
def mark_sent(order: PurchaseOrder, *, actor=None) -> PurchaseOrder:
    if order.status != PurchaseOrderStatus.APPROVED:
        raise InvalidStateTransition(
            _("Only an approved order can be sent to a supplier."),
            details={"status": order.status},
        )
    order.status = PurchaseOrderStatus.SENT
    order.sent_at = timezone.now()
    order.updated_by = actor
    order.save(update_fields=["status", "sent_at", "updated_by", "updated_at"])

    record(
        AuditAction.UPDATE,
        PurchaseOrder._meta.label,
        entity_id=str(order.pk),
        entity_label=order.order_number,
        new_value={"status": order.status},
        changed_fields=["status"],
        notes="Order sent to supplier",
        actor=actor,
    )
    return order


def _apportion_landed_costs(order: PurchaseOrder, line_value: Decimal, order_goods_value: Decimal) -> Decimal:
    """
    Share of import charges attributable to a line, apportioned by value.

    Value-based apportionment (rather than by weight or unit count) is the
    convention that keeps high-value, low-volume pharmaceuticals from being
    under-costed. It is an approximation — freight genuinely correlates with
    volume — but it is the standard basis and it is consistent, which matters
    more than theoretical precision for inventory valuation.
    """
    charges = order.freight_cost + order.customs_duty + order.other_charges
    if charges <= ZERO or order_goods_value <= ZERO:
        return ZERO
    return q_internal(charges * (line_value / order_goods_value))


@transaction.atomic
def receive_goods(
    order: PurchaseOrder,
    *,
    lines: list[dict],
    delivery_note_number: str = "",
    receipt_date=None,
    quality_checked: bool = False,
    quality_notes: str = "",
    actor=None,
) -> GoodsReceipt:
    """
    Book a delivery into stock.

    Each line dict: purchase_order_line, batch_number, expiry_date,
    quantity_received, unit_cost (optional), manufacturing_date (optional),
    quantity_rejected (optional), rejection_reason (optional).

    Creates real StockBatch rows through the inventory service, so every
    received unit lands in the stock ledger with a landed cost that includes
    its share of freight and duty.
    """
    if order.status not in RECEIVABLE_STATUSES:
        raise InvalidStateTransition(
            _("Goods cannot be received against an order with status %(status)s.")
            % {"status": order.get_status_display()},
            details={"status": order.status},
        )
    if not lines:
        raise BusinessRuleViolation(
            _("A goods receipt must contain at least one line."), code="empty_receipt"
        )

    receipt_date = receipt_date or timezone.now()

    receipt = GoodsReceipt.objects.create(
        receipt_number=next_number("purchasing.receipt", when=receipt_date),
        purchase_order=order,
        warehouse=order.warehouse,
        receipt_date=receipt_date,
        delivery_note_number=delivery_note_number,
        received_by=actor,
        quality_checked=quality_checked,
        quality_checked_by=actor if quality_checked else None,
        quality_notes=quality_notes,
        created_by=actor,
    )

    # Basis for apportionment: the goods value of this delivery.
    order_goods_value = sum_money(
        q_internal(d["quantity_received"])
        * q_internal(d.get("unit_cost") or d["purchase_order_line"].unit_cost)
        for d in lines
    )

    from apps.inventory.services import receive_stock

    for data in lines:
        po_line: PurchaseOrderLine = data["purchase_order_line"]
        quantity = q_internal(data["quantity_received"])
        rejected = q_internal(data.get("quantity_rejected", ZERO))
        unit_cost = q_internal(data.get("unit_cost") or po_line.unit_cost)

        if quantity <= ZERO:
            raise BusinessRuleViolation(
                _("Received quantities must be greater than zero."), code="invalid_quantity"
            )

        outstanding = po_line.quantity_outstanding
        if quantity > outstanding:
            raise BusinessRuleViolation(
                _("Cannot receive %(qty)s of %(product)s: only %(max)s remain outstanding on this order line.")
                % {
                    "qty": quantity,
                    "product": po_line.product.name,
                    "max": outstanding,
                },
                code="over_receipt",
                details={
                    "ordered": str(po_line.quantity_ordered),
                    "already_received": str(po_line.quantity_received),
                    "outstanding": str(outstanding),
                },
            )

        expiry_date = data["expiry_date"]
        # Short-dated stock is a commercial trap: it arrives sellable but
        # becomes a write-off before it can move. The order line may state a
        # minimum acceptable expiry, and delivering inside it is refused.
        if po_line.expected_expiry_date and expiry_date < po_line.expected_expiry_date:
            raise BusinessRuleViolation(
                _("Batch %(batch)s expires on %(actual)s, before the minimum acceptable date of %(expected)s for this line.")
                % {
                    "batch": data["batch_number"],
                    "actual": expiry_date,
                    "expected": po_line.expected_expiry_date,
                },
                code="expiry_below_minimum",
            )

        line_value = q_internal(quantity * unit_cost)
        apportioned = _apportion_landed_costs(order, line_value, order_goods_value)
        landed_unit_cost = q_internal(
            unit_cost + (apportioned / quantity if quantity else ZERO)
        )

        batch, _movement = receive_stock(
            product=po_line.product,
            warehouse=order.warehouse,
            batch_number=data["batch_number"],
            expiry_date=expiry_date,
            quantity=quantity,
            unit_cost=unit_cost,
            landed_unit_cost=landed_unit_cost,
            supplier=order.supplier,
            manufacturing_date=data.get("manufacturing_date"),
            purchase_order=order,
            source_reference=receipt.receipt_number,
            performed_by=actor,
        )

        GoodsReceiptLine.objects.create(
            goods_receipt=receipt,
            purchase_order_line=po_line,
            product=po_line.product,
            batch=batch,
            batch_number=data["batch_number"],
            manufacturing_date=data.get("manufacturing_date"),
            expiry_date=expiry_date,
            quantity_received=quantity,
            quantity_rejected=rejected,
            rejection_reason=data.get("rejection_reason", ""),
            unit_cost=unit_cost,
            landed_unit_cost=landed_unit_cost,
        )

        po_line.quantity_received = q_internal(po_line.quantity_received + quantity)
        po_line.save(update_fields=["quantity_received"])

    # Refresh order status from line completion.
    order.refresh_from_db()
    if order.is_fully_received:
        order.status = PurchaseOrderStatus.RECEIVED
        order.actual_delivery_date = receipt_date.date()
    else:
        order.status = PurchaseOrderStatus.PARTIALLY_RECEIVED
    order.updated_by = actor
    order.save(update_fields=["status", "actual_delivery_date", "updated_by", "updated_at"])

    record(
        AuditAction.CREATE,
        GoodsReceipt._meta.label,
        entity_id=str(receipt.pk),
        entity_label=receipt.receipt_number,
        new_value={
            "purchase_order": order.order_number,
            "lines": len(lines),
            "order_status": order.status,
            "quality_checked": quality_checked,
        },
        notes=f"Goods received against {order.order_number}",
        actor=actor,
    )
    return receipt


@transaction.atomic
def cancel_order(order: PurchaseOrder, *, reason: str, actor=None) -> PurchaseOrder:
    """
    Cancel an order.

    Blocked once any goods have been received: the received stock is real and
    already valued, so the order must be closed short rather than erased.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to cancel an order."), code="reason_required"
        )
    if order.status in {PurchaseOrderStatus.CANCELLED, PurchaseOrderStatus.RECEIVED}:
        raise InvalidStateTransition(
            _("This order cannot be cancelled in its current state."),
            details={"status": order.status},
        )
    if order.receipts.exists():
        raise BusinessRuleViolation(
            _("Goods have already been received against this order. Close it short instead of cancelling."),
            code="order_has_receipts",
        )

    previous = order.status
    order.status = PurchaseOrderStatus.CANCELLED
    order.cancellation_reason = reason
    order.updated_by = actor
    order.save(update_fields=["status", "cancellation_reason", "updated_by", "updated_at"])

    record(
        AuditAction.CANCEL,
        PurchaseOrder._meta.label,
        entity_id=str(order.pk),
        entity_label=order.order_number,
        previous_value={"status": previous},
        new_value={"status": order.status},
        changed_fields=["status"],
        notes=f"Purchase order cancelled: {reason}",
        actor=actor,
    )
    return order


@transaction.atomic
def record_supplier_invoice(
    order: PurchaseOrder, *, invoice_number: str, invoice_date, actor=None
) -> PurchaseOrder:
    """Attach the supplier's invoice reference for three-way matching."""
    order.supplier_invoice_number = invoice_number
    order.supplier_invoice_date = invoice_date
    order.updated_by = actor
    order.save(
        update_fields=[
            "supplier_invoice_number", "supplier_invoice_date", "updated_by", "updated_at",
        ]
    )

    record(
        AuditAction.UPDATE,
        PurchaseOrder._meta.label,
        entity_id=str(order.pk),
        entity_label=order.order_number,
        new_value={"supplier_invoice_number": invoice_number},
        changed_fields=["supplier_invoice_number"],
        notes=f"Supplier invoice {invoice_number} recorded",
        actor=actor,
    )
    return order


# ---------------------------------------------------------------------------
# Payables: supplier invoices and their settlement
# ---------------------------------------------------------------------------


@transaction.atomic
def create_supplier_invoice(
    *,
    supplier,
    invoice_number: str,
    total_amount: Decimal | None = None,
    subtotal: Decimal = ZERO,
    tax_amount: Decimal = ZERO,
    freight_cost: Decimal = ZERO,
    customs_duty: Decimal = ZERO,
    other_charges: Decimal = ZERO,
    invoice_date=None,
    received_date=None,
    due_date=None,
    purchase_order: PurchaseOrder | None = None,
    currency: str = "",
    exchange_rate: Decimal | None = None,
    notes: str = "",
    actor=None,
) -> SupplierInvoice:
    """
    Record a bill received from a supplier.

    `total_amount` may be given explicitly or left to be derived from the
    component amounts. Both are supported deliberately: a clerk copying a
    foreign invoice usually has only the grand total in front of them, while an
    invoice matched against an order is entered component by component. When
    the caller supplies a total, it wins — the supplier's stated total is the
    authoritative figure even where it does not equal the sum of its parts, and
    quietly recomputing it would hide exactly the discrepancy that matters.

    The due date falls back to the supplier's payment terms, which is what
    makes the outstanding-balance and overdue reports work without the clerk
    having to compute a date on every entry.
    """
    invoice_date = invoice_date or timezone.localdate()

    if not invoice_number or not invoice_number.strip():
        raise BusinessRuleViolation(
            _("The supplier's invoice number is required."), code="invoice_number_required"
        )
    invoice_number = invoice_number.strip()

    if purchase_order is not None and purchase_order.supplier_id != supplier.pk:
        raise BusinessRuleViolation(
            _("Purchase order %(order)s belongs to a different supplier.")
            % {"order": purchase_order.order_number},
            code="order_supplier_mismatch",
        )

    # The same supplier billing the same number twice is nearly always a
    # duplicate entry, and paying it twice is the expensive outcome the check
    # exists to prevent. Reported here rather than left to the unique
    # constraint so the operator gets the existing document, not a 500.
    duplicate = SupplierInvoice.objects.filter(
        supplier=supplier, invoice_number=invoice_number
    ).first()
    if duplicate is not None:
        raise BusinessRuleViolation(
            _("Invoice %(number)s from %(supplier)s has already been recorded as %(reference)s.")
            % {
                "number": invoice_number,
                "supplier": supplier.name,
                "reference": duplicate.reference,
            },
            code="duplicate_supplier_invoice",
            details={
                "supplier_invoice_id": str(duplicate.pk),
                "reference": duplicate.reference,
            },
        )

    subtotal = q_internal(subtotal)
    tax_amount = q_internal(tax_amount)
    freight_cost = q_internal(freight_cost)
    customs_duty = q_internal(customs_duty)
    other_charges = q_internal(other_charges)

    if total_amount is None:
        total_amount = sum_money(
            [subtotal, tax_amount, freight_cost, customs_duty, other_charges]
        )
    else:
        total_amount = q_internal(total_amount)

    if total_amount <= ZERO:
        raise BusinessRuleViolation(
            _("A supplier invoice must be for more than zero."), code="invalid_amount"
        )

    if due_date is None:
        due_date = invoice_date + timedelta(days=supplier.payment_term_days)

    invoice = SupplierInvoice.objects.create(
        reference=next_number("purchasing.supplier_invoice", when=invoice_date),
        invoice_number=invoice_number,
        supplier=supplier,
        purchase_order=purchase_order,
        status=SupplierInvoiceStatus.AWAITING_PAYMENT,
        invoice_date=invoice_date,
        received_date=received_date or timezone.localdate(),
        due_date=due_date,
        subtotal=subtotal,
        tax_amount=tax_amount,
        freight_cost=freight_cost,
        customs_duty=customs_duty,
        other_charges=other_charges,
        total_amount=total_amount,
        paid_amount=ZERO,
        balance_due=total_amount,
        currency=currency or supplier.currency,
        exchange_rate=q_internal(exchange_rate) if exchange_rate is not None else Decimal("1"),
        notes=notes,
        created_by=actor,
    )

    # Keep the order's own reference fields in step, so three-way matching from
    # the order side still works for anyone reading it.
    if purchase_order is not None and not purchase_order.supplier_invoice_number:
        purchase_order.supplier_invoice_number = invoice_number
        purchase_order.supplier_invoice_date = invoice_date
        purchase_order.save(
            update_fields=[
                "supplier_invoice_number", "supplier_invoice_date", "updated_at",
            ]
        )

    record(
        AuditAction.CREATE,
        SupplierInvoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.reference,
        new_value=snapshot(invoice),
        notes=(
            f"Supplier invoice {invoice_number} from {supplier.name} recorded: "
            f"{total_amount} {invoice.currency}"
        ),
        actor=actor,
    )
    return invoice


def _recompute_supplier_invoice_state(invoice: SupplierInvoice) -> SupplierInvoice:
    """
    Re-derive an invoice's paid amount, balance and status from its allocations.

    Derived on every change rather than incremented, for the same reason
    `partners.recompute_balance` is: a running tally silently accumulates error
    from every reversal and correction, and a wrong payable balance is only
    discovered when a supplier disputes their statement.

    Allocations belonging to reversed payments are excluded — the money came
    back, so it no longer settles anything.
    """
    paid = (
        SupplierPaymentAllocation.objects.filter(
            supplier_invoice=invoice, payment__is_reversed=False,
        ).aggregate(total=models.Sum("amount"))["total"]
        or ZERO
    )
    paid = q_internal(paid)

    invoice.paid_amount = paid
    invoice.balance_due = q_internal(invoice.total_amount - paid)

    # A cancelled invoice keeps that status regardless of what was paid against
    # it; the cancellation is a decision about the document, not a balance.
    if invoice.status != SupplierInvoiceStatus.CANCELLED:
        if invoice.balance_due <= ZERO:
            invoice.status = SupplierInvoiceStatus.PAID
        elif paid > ZERO:
            invoice.status = SupplierInvoiceStatus.PARTIALLY_PAID
        elif invoice.due_date and invoice.due_date < timezone.localdate():
            invoice.status = SupplierInvoiceStatus.OVERDUE
        else:
            invoice.status = SupplierInvoiceStatus.AWAITING_PAYMENT

    invoice.save(
        update_fields=["paid_amount", "balance_due", "status", "updated_at"]
    )
    return invoice


def _sync_supplier_payment_allocated(payment: SupplierPayment) -> SupplierPayment:
    """Bring a payment's allocated_amount back in line with its allocations."""
    allocated = (
        payment.allocations.aggregate(total=models.Sum("amount"))["total"] or ZERO
    )
    payment.allocated_amount = q_internal(allocated)
    payment.save(update_fields=["allocated_amount", "updated_at"])
    return payment


def _settleable_supplier_invoices(supplier, invoice_ids=None):
    """
    Open invoices for a supplier, oldest due first.

    Oldest-first is the default because it is what avoids late-payment
    penalties and keeps a supplier relationship intact. When specific invoices
    are named, that order is preserved among them.
    """
    queryset = SupplierInvoice.objects.select_for_update().filter(
        supplier=supplier,
        status__in=OPEN_SUPPLIER_INVOICE_STATUSES,
        deleted_at__isnull=True,
    )
    if invoice_ids:
        queryset = queryset.filter(pk__in=invoice_ids)
    # Nulls last: an invoice with no due date is not more urgent than one with
    # a date that has passed.
    return queryset.order_by(models.F("due_date").asc(nulls_last=True), "invoice_date")


@transaction.atomic
def record_supplier_payment(
    *,
    supplier,
    amount: Decimal,
    method: str = SupplierPaymentMethod.BANK_TRANSFER,
    payment_date=None,
    payment_reference: str = "",
    bank_reference: str = "",
    bank_account: str = "",
    notes: str = "",
    invoice_ids: list | None = None,
    allocations: list[dict] | None = None,
    actor=None,
) -> SupplierPayment:
    """
    Record a payment to a supplier and allocate it against their open invoices.

    Three ways to say where the money goes, in decreasing specificity:

      * `allocations` — explicit amounts per invoice. Used when a payment
        settles part of one invoice and all of another, which no automatic
        rule can infer.
      * `invoice_ids` — settle these invoices, oldest due first.
      * neither — settle the supplier's open invoices, oldest due first.

    Anything left over stays on the payment as unallocated credit: money paid
    on account, visible on the supplier's balance and available to allocate
    when the next invoice arrives. It is not silently discarded, because it is
    real money that left the bank.

    Naming an invoice that cannot take the money is refused rather than
    quietly re-aimed — the same rule as on the customer side. Silently paying a
    different invoice produces a correct-looking record that settles the wrong
    debt, and nobody finds out until a reconciliation.
    """
    amount = q_internal(amount)
    if amount <= ZERO:
        raise BusinessRuleViolation(
            _("A payment amount must be greater than zero."), code="invalid_amount"
        )

    payment_date = payment_date or timezone.localdate()

    payment = SupplierPayment.objects.create(
        reference=next_number("purchasing.supplier_payment", when=payment_date),
        supplier=supplier,
        payment_date=payment_date,
        amount=amount,
        method=method,
        payment_reference=payment_reference,
        bank_reference=bank_reference,
        bank_account=bank_account,
        notes=notes,
        paid_by=actor,
        created_by=actor,
    )

    if allocations:
        remaining = _allocate_supplier_payment_explicit(
            payment, supplier, allocations, amount, actor=actor
        )
    else:
        open_invoices = list(_settleable_supplier_invoices(supplier, invoice_ids))
        if invoice_ids:
            _reject_unsettleable_supplier_invoices(supplier, invoice_ids, open_invoices)
        remaining = _allocate_supplier_payment(payment, open_invoices, amount, actor=actor)

    _sync_supplier_payment_allocated(payment)

    record(
        AuditAction.CREATE,
        SupplierPayment._meta.label,
        entity_id=str(payment.pk),
        entity_label=payment.reference,
        new_value={
            "supplier": supplier.supplier_code,
            "amount": str(amount),
            "allocated": str(payment.allocated_amount),
            "unallocated": str(remaining),
            "method": method,
            "bank_reference": bank_reference,
        },
        notes=f"Supplier payment recorded: {payment.reference} to {supplier.name}",
        actor=actor,
    )
    return payment


def _allocate_supplier_payment(
    payment: SupplierPayment, invoices, amount: Decimal, *, actor=None
) -> Decimal:
    """Spread `amount` across `invoices` in order, settling each in turn."""
    remaining = amount

    for invoice in invoices:
        if remaining <= ZERO:
            break
        applied = min(remaining, invoice.balance_due)
        if applied <= ZERO:
            continue

        SupplierPaymentAllocation.objects.create(
            payment=payment,
            supplier_invoice=invoice,
            amount=applied,
            created_by=actor,
        )
        _recompute_supplier_invoice_state(invoice)
        remaining = q_internal(remaining - applied)

    return remaining


def _allocate_supplier_payment_explicit(
    payment: SupplierPayment, supplier, allocations: list[dict], amount: Decimal, *, actor=None
) -> Decimal:
    """
    Apply caller-specified amounts to named invoices.

    Every line is validated before any is written, so a payload with one bad
    line does not leave the payment half-allocated.
    """
    invoice_ids = [entry["supplier_invoice"] for entry in allocations]
    invoices = {
        inv.pk: inv
        for inv in SupplierInvoice.objects.select_for_update().filter(
            pk__in=invoice_ids, supplier=supplier, deleted_at__isnull=True
        )
    }

    missing = set(invoice_ids) - set(invoices)
    if missing:
        raise BusinessRuleViolation(
            _("One or more invoices do not exist or belong to a different supplier."),
            code="invalid_supplier_invoice",
            details={"supplier_invoice_ids": [str(m) for m in missing]},
        )

    total_requested = sum_money(q_internal(entry["amount"]) for entry in allocations)
    if total_requested > amount:
        raise BusinessRuleViolation(
            _("The allocations total %(allocated)s, which is more than the payment of %(amount)s.")
            % {"allocated": total_requested, "amount": amount},
            code="allocation_exceeds_payment",
            details={"allocated": str(total_requested), "payment_amount": str(amount)},
        )

    for entry in allocations:
        invoice = invoices[entry["supplier_invoice"]]
        applied = q_internal(entry["amount"])

        if applied <= ZERO:
            raise BusinessRuleViolation(
                _("An allocated amount must be greater than zero."),
                code="invalid_allocation_amount",
            )
        if invoice.is_cancelled:
            raise BusinessRuleViolation(
                _("Invoice %(number)s has been cancelled and cannot be paid.")
                % {"number": invoice.invoice_number},
                code="invoice_cancelled",
            )
        # Overpaying a specific invoice is refused rather than spilled onto the
        # next one: the operator named this invoice and this amount, and
        # quietly moving the excess elsewhere contradicts an explicit
        # instruction.
        if applied > invoice.balance_due:
            raise BusinessRuleViolation(
                _("Cannot allocate %(amount)s to invoice %(number)s: only %(balance)s is outstanding.")
                % {
                    "amount": applied,
                    "number": invoice.invoice_number,
                    "balance": invoice.balance_due,
                },
                code="allocation_exceeds_balance",
                details={
                    "supplier_invoice_id": str(invoice.pk),
                    "balance_due": str(invoice.balance_due),
                },
            )

    for entry in allocations:
        invoice = invoices[entry["supplier_invoice"]]
        SupplierPaymentAllocation.objects.create(
            payment=payment,
            supplier_invoice=invoice,
            amount=q_internal(entry["amount"]),
            created_by=actor,
        )
        _recompute_supplier_invoice_state(invoice)

    return q_internal(amount - total_requested)


def _reject_unsettleable_supplier_invoices(supplier, invoice_ids, open_invoices) -> None:
    """Refuse named invoices that cannot take money, explaining which and why."""
    settleable = {inv.pk for inv in open_invoices}
    unsettleable = [pk for pk in invoice_ids if pk not in settleable]
    if not unsettleable:
        return

    known = {
        inv.pk: inv
        for inv in SupplierInvoice.objects.filter(pk__in=unsettleable, deleted_at__isnull=True)
    }
    details = {}
    for pk in unsettleable:
        invoice = known.get(pk)
        if invoice is None:
            details[str(pk)] = "not found"
        elif invoice.supplier_id != supplier.pk:
            details[str(pk)] = "belongs to another supplier"
        else:
            details[str(pk)] = f"status {invoice.status}"

    raise BusinessRuleViolation(
        _("One or more of the invoices named cannot receive a payment."),
        code="unsettleable_supplier_invoice",
        details={"invoices": details},
    )


@transaction.atomic
def allocate_supplier_payment(
    payment: SupplierPayment, *, allocations: list[dict], actor=None
) -> SupplierPayment:
    """
    Allocate a payment's unallocated remainder to invoices.

    The counterpart to paying on account: money paid ahead of an invoice is
    matched to it once it arrives, without a second payment being invented.
    """
    if payment.is_reversed:
        raise BusinessRuleViolation(
            _("Payment %(reference)s has been reversed and cannot be allocated.")
            % {"reference": payment.reference},
            code="payment_reversed",
        )

    available = payment.unallocated_amount
    if available <= ZERO:
        raise BusinessRuleViolation(
            _("Payment %(reference)s is already fully allocated.")
            % {"reference": payment.reference},
            code="payment_fully_allocated",
        )

    requested = sum_money(q_internal(entry["amount"]) for entry in allocations)
    if requested > available:
        raise BusinessRuleViolation(
            _("Only %(available)s of this payment is unallocated; %(requested)s was requested.")
            % {"available": available, "requested": requested},
            code="allocation_exceeds_unallocated",
            details={"unallocated": str(available), "requested": str(requested)},
        )

    _allocate_supplier_payment_explicit(
        payment, payment.supplier, allocations, available, actor=actor
    )
    _sync_supplier_payment_allocated(payment)

    record(
        AuditAction.UPDATE,
        SupplierPayment._meta.label,
        entity_id=str(payment.pk),
        entity_label=payment.reference,
        new_value={
            "allocated": str(payment.allocated_amount),
            "unallocated": str(payment.unallocated_amount),
        },
        notes=f"Supplier payment {payment.reference} allocated",
        actor=actor,
    )
    return payment


@transaction.atomic
def reverse_supplier_payment(
    payment: SupplierPayment, *, reason: str, actor=None
) -> SupplierPayment:
    """
    Reverse a supplier payment — a bounced cheque, or an entry made in error.

    The payment and its allocations are retained and the row is flagged, rather
    than deleted. Money that moved and came back is two events in the cash
    record, and erasing the first would make the bank statement impossible to
    reconcile against our books. The invoices it touched are re-derived, which
    reopens them: `_recompute_supplier_invoice_state` ignores allocations
    belonging to a reversed payment.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to reverse a payment."), code="reason_required"
        )
    if payment.is_reversed:
        raise InvalidStateTransition(
            _("Payment %(reference)s has already been reversed.")
            % {"reference": payment.reference},
            details={"reference": payment.reference},
        )

    affected = list(
        SupplierInvoice.objects.select_for_update().filter(
            payment_allocations__payment=payment
        ).distinct()
    )

    payment.is_reversed = True
    payment.reversed_at = timezone.now()
    payment.reversal_reason = reason
    payment.updated_by = actor
    payment.save(
        update_fields=[
            "is_reversed", "reversed_at", "reversal_reason", "updated_by", "updated_at",
        ]
    )

    for invoice in affected:
        _recompute_supplier_invoice_state(invoice)

    record(
        AuditAction.CANCEL,
        SupplierPayment._meta.label,
        entity_id=str(payment.pk),
        entity_label=payment.reference,
        previous_value={"is_reversed": False},
        new_value={"is_reversed": True, "amount": str(payment.amount)},
        changed_fields=["is_reversed"],
        notes=(
            f"Supplier payment reversed: {reason} "
            f"({len(affected)} invoice(s) reopened)"
        ),
        actor=actor,
    )
    return payment


@transaction.atomic
def cancel_supplier_invoice(
    invoice: SupplierInvoice, *, reason: str, actor=None
) -> SupplierInvoice:
    """
    Cancel a supplier invoice recorded in error.

    Refused once money has been paid against it: the payment would be left
    settling a document that no longer exists. Reverse the payment first, which
    is the deliberate two-step that keeps the cash record honest.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to cancel an invoice."), code="reason_required"
        )
    if invoice.is_cancelled:
        raise InvalidStateTransition(
            _("This invoice has already been cancelled."),
            details={"status": invoice.status},
        )
    if invoice.paid_amount > ZERO:
        raise BusinessRuleViolation(
            _("Invoice %(number)s has payments totalling %(paid)s against it. "
              "Reverse them before cancelling.")
            % {"number": invoice.invoice_number, "paid": invoice.paid_amount},
            code="invoice_has_payments",
            details={"paid_amount": str(invoice.paid_amount)},
        )

    previous_status = invoice.status
    invoice.status = SupplierInvoiceStatus.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.cancellation_reason = reason
    invoice.balance_due = ZERO
    invoice.updated_by = actor
    invoice.save(
        update_fields=[
            "status", "cancelled_at", "cancellation_reason", "balance_due",
            "updated_by", "updated_at",
        ]
    )

    record(
        AuditAction.CANCEL,
        SupplierInvoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.reference,
        previous_value={"status": previous_status},
        new_value={"status": SupplierInvoiceStatus.CANCELLED},
        changed_fields=["status"],
        notes=f"Supplier invoice cancelled: {reason}",
        actor=actor,
    )
    return invoice


def supplier_outstanding_balance(supplier) -> Decimal:
    """
    What is currently owed to a supplier.

    Derived from open invoices on every call rather than stored on the supplier
    row. There is no cached field to drift, and the figure is cheap enough at
    this scale that correctness is the better trade — the same reasoning as
    `partners.recompute_balance`, arrived at from the opposite direction.
    """
    total = (
        SupplierInvoice.objects.filter(
            supplier=supplier,
            status__in=OPEN_SUPPLIER_INVOICE_STATUSES,
            deleted_at__isnull=True,
        ).aggregate(total=models.Sum("balance_due"))["total"]
        or ZERO
    )
    return q_internal(total)


def supplier_balances(*, only_outstanding: bool = True) -> list[dict]:
    """
    Outstanding payables for every supplier, largest exposure first.

    The "outstanding supplier balances" report, and the data behind the
    supplier picker on the payment screen. Aggregated in the database rather
    than by looping suppliers in Python: a per-supplier query here would be a
    round trip for every row on a page that lists them all.

    `total_invoiced` and `total_paid` cover open invoices only, so the three
    figures on a row reconcile with each other. A supplier's full historical
    turnover is a different question, answered by the payment report.
    """
    today = timezone.localdate()

    rows = (
        SupplierInvoice.objects.filter(
            status__in=OPEN_SUPPLIER_INVOICE_STATUSES, deleted_at__isnull=True,
        )
        .values(
            "supplier_id",
            "supplier__supplier_code",
            "supplier__name",
            "supplier__currency",
        )
        .annotate(
            invoice_count=models.Count("id"),
            total_invoiced=models.Sum("total_amount"),
            total_paid=models.Sum("paid_amount"),
            outstanding_balance=models.Sum("balance_due"),
            overdue_amount=models.Sum(
                "balance_due",
                filter=models.Q(due_date__lt=today),
                default=ZERO,
            ),
            oldest_due_date=models.Min("due_date"),
        )
        .order_by("-outstanding_balance")
    )

    if only_outstanding:
        rows = rows.filter(balance_due__gt=ZERO)

    return [
        {
            "supplier_id": str(row["supplier_id"]),
            "supplier_code": row["supplier__supplier_code"],
            "supplier_name": row["supplier__name"],
            "currency": row["supplier__currency"],
            "invoice_count": row["invoice_count"],
            "total_invoiced": q_internal(row["total_invoiced"] or ZERO),
            "total_paid": q_internal(row["total_paid"] or ZERO),
            "outstanding_balance": q_internal(row["outstanding_balance"] or ZERO),
            "overdue_amount": q_internal(row["overdue_amount"] or ZERO),
            "oldest_due_date": row["oldest_due_date"],
        }
        for row in rows
    ]


def supplier_payment_history(supplier, *, date_from=None, date_to=None) -> list[dict]:
    """
    Every payment made to a supplier, most recent first.

    Each row carries the invoices it settled, so the history answers "what did
    we pay them, and what for" in one pass rather than requiring a follow-up
    query per payment.
    """
    payments = (
        SupplierPayment.objects.filter(supplier=supplier, deleted_at__isnull=True)
        .prefetch_related("allocations__supplier_invoice")
        .order_by("-payment_date", "-reference")
    )
    if date_from:
        payments = payments.filter(payment_date__gte=date_from)
    if date_to:
        payments = payments.filter(payment_date__lte=date_to)

    return [
        {
            "id": str(payment.pk),
            "reference": payment.reference,
            "payment_date": payment.payment_date,
            "amount": payment.amount,
            "allocated_amount": payment.allocated_amount,
            "unallocated_amount": payment.unallocated_amount,
            "method": payment.method,
            "payment_reference": payment.payment_reference,
            "bank_reference": payment.bank_reference,
            "is_reversed": payment.is_reversed,
            "notes": payment.notes,
            "allocations": [
                {
                    "supplier_invoice_id": str(allocation.supplier_invoice_id),
                    "invoice_number": allocation.supplier_invoice.invoice_number,
                    "reference": allocation.supplier_invoice.reference,
                    "amount": allocation.amount,
                }
                for allocation in payment.allocations.all()
            ],
        }
        for payment in payments
    ]
