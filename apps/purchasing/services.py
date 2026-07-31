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

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record, snapshot
from apps.core.exceptions import BusinessRuleViolation, DocumentLocked, InvalidStateTransition
from apps.core.models import AuditAction
from apps.core.money import compute_line, q_internal, sum_money
from apps.core.numbering import next_number

from .models import (
    RECEIVABLE_STATUSES,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
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
