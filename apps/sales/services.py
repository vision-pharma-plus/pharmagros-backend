"""
Sales orchestration.

`confirm_sale` is the most consequential function in the system: it checks
credit, issues stock via FIFO, records batch traceability, generates the
invoice and updates the customer balance â€” all in one transaction. Either the
whole commercial event happens or none of it does. A partial commit here would
mean stock left the warehouse without an invoice, or an invoice existed for
goods never issued.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record, snapshot
from apps.core.exceptions import (
    BusinessRuleViolation,
    InvalidStateTransition,
)
from apps.core.models import AuditAction
from apps.core.money import compute_line, q_internal, sum_money
from apps.core.numbering import next_number
from apps.inventory.models import MovementType
from apps.inventory.services import (
    allocate_fifo,
    post_batch_movement,
    release_reservations,
)

from .models import (
    Sale,
    SaleLine,
    SaleLineBatch,
    SaleReturn,
    SaleReturnLine,
    SaleStatus,
    SaleType,
)

ZERO = Decimal("0")


@transaction.atomic
def create_sale(
    *,
    customer,
    warehouse,
    lines: list[dict],
    sale_type: str = SaleType.CASH,
    salesperson=None,
    sale_date=None,
    discount_percent: Decimal = ZERO,
    customer_order_reference: str = "",
    notes: str = "",
    actor=None,
) -> Sale:
    """
    Create a DRAFT sale.

    No stock moves and no invoice is produced here â€” a draft is a quotation
    in effect. Everything commercial happens at confirm_sale.

    Each line dict: product, quantity, unit_price (optional â€” resolved from
    the catalogue), discount_percent (optional).
    """
    if not lines:
        raise BusinessRuleViolation(
            _("A sale must contain at least one line."), code="empty_sale"
        )

    sale_date = sale_date or timezone.now()

    # Supplying a pharmacy whose operating licence has lapsed exposes the
    # wholesaler to regulatory action, so it is refused at creation rather
    # than discovered at delivery.
    if customer.licence_is_expired:
        raise BusinessRuleViolation(
            _("The operating licence for %(name)s expired on %(date)s. The sale cannot proceed until it is renewed.")
            % {"name": customer.business_name, "date": customer.licence_expiry},
            code="customer_licence_expired",
            details={"licence_expiry": str(customer.licence_expiry)},
        )

    sale = Sale.objects.create(
        sale_number=next_number("sales.order", when=sale_date),
        sale_type=sale_type,
        status=SaleStatus.DRAFT,
        customer=customer,
        warehouse=warehouse,
        sale_date=sale_date,
        discount_percent=q_internal(discount_percent),
        salesperson=salesperson or actor,
        delivery_address=customer.address,
        customer_order_reference=customer_order_reference,
        notes=notes,
        created_by=actor,
    )

    for index, data in enumerate(lines, start=1):
        _build_line(sale, index, data, customer)

    _recalculate_sale(sale)
    sale.save()

    record(
        AuditAction.CREATE,
        Sale._meta.label,
        entity_id=str(sale.pk),
        entity_label=sale.sale_number,
        new_value=snapshot(sale),
        actor=actor,
    )
    return sale


def _build_line(sale: Sale, line_number: int, data: dict, customer) -> SaleLine:
    """Create a sale line, resolving price and VAT from the catalogue."""
    product = data["product"]
    quantity = q_internal(data["quantity"])

    if quantity <= ZERO:
        raise BusinessRuleViolation(
            _("Line quantities must be greater than zero."), code="invalid_quantity"
        )

    # Price resolution order: explicit override â†’ customer-tier price.
    unit_price = data.get("unit_price")
    unit_price = q_internal(unit_price) if unit_price is not None else product.price_for(customer)

    # Line discount falls back to the customer's standing discount.
    discount_percent = data.get("discount_percent")
    if discount_percent is None:
        discount_percent = customer.discount_percent
    discount_percent = q_internal(discount_percent)

    tax_rate = q_internal(product.effective_vat_rate)
    amounts = compute_line(quantity, unit_price, discount_percent, tax_rate)

    return SaleLine.objects.create(
        sale=sale,
        line_number=line_number,
        product=product,
        quantity=quantity,
        unit_price=unit_price,
        discount_percent=discount_percent,
        discount_amount=amounts["discount"],
        tax_rate=tax_rate,
        tax_amount=amounts["tax"],
        line_subtotal=amounts["gross"],
        line_total=amounts["total"],
        # unit_cost is filled at confirmation, once the actual batches are known.
        unit_cost=ZERO,
        line_cost=ZERO,
    )


def _recalculate_sale(sale: Sale) -> Sale:
    """Roll line amounts up to the header."""
    lines = list(sale.lines.all())
    sale.subtotal = sum_money(line.line_subtotal for line in lines)
    sale.discount_amount = sum_money(line.discount_amount for line in lines)
    sale.tax_amount = sum_money(line.tax_amount for line in lines)
    sale.total_amount = sum_money(line.line_total for line in lines)
    sale.total_cost = sum_money(line.line_cost for line in lines)
    return sale


@transaction.atomic
def confirm_sale(
    sale: Sale,
    *,
    actor=None,
    credit_override_reason: str = "",
    credit_override_by=None,
    generate_invoice: bool = True,
):
    """
    Confirm a sale: check credit, issue stock, record traceability, invoice.

    Ordering is deliberate. Credit is checked *before* stock moves, so a
    refused sale never leaves the warehouse short. Stock is issued before the
    invoice is posted, so an invoice can never exist for goods that could not
    be supplied.
    """
    if sale.status != SaleStatus.DRAFT:
        raise InvalidStateTransition(
            _("Only a draft sale can be confirmed; this one is %(status)s.")
            % {"status": sale.get_status_display()},
            details={"current_status": sale.status},
        )

    lines = list(sale.lines.select_related("product"))
    if not lines:
        raise BusinessRuleViolation(
            _("A sale cannot be confirmed without lines."), code="empty_sale"
        )

    customer = sale.customer

    # --- 1. Credit gate -------------------------------------------------
    if sale.sale_type == SaleType.CREDIT:
        allowed, reason = customer.can_buy_on_credit(sale.total_amount)
        if not allowed:
            if not credit_override_reason:
                from apps.core.exceptions import CreditLimitExceeded

                raise CreditLimitExceeded(
                    reason,
                    details={
                        "customer_code": customer.customer_code,
                        "credit_limit": str(customer.credit_limit),
                        "outstanding_balance": str(customer.outstanding_balance),
                        "available_credit": str(customer.available_credit),
                        "sale_total": str(sale.total_amount),
                    },
                )
            # An override is a deliberate assumption of risk; it must be
            # attributable to a specific authoriser, not just "someone".
            if credit_override_by is None:
                raise BusinessRuleViolation(
                    _("A credit limit override must identify the authorising user."),
                    code="override_authoriser_required",
                )
            sale.credit_override_by = credit_override_by
            sale.credit_override_reason = credit_override_reason

    # --- 2. Issue stock, capturing real batch costs ---------------------
    for line in lines:
        allocations = allocate_fifo(
            line.product, line.quantity, warehouse=sale.warehouse, lock=True,
        )

        line_cost = ZERO
        for allocation in allocations:
            post_batch_movement(
                allocation.batch,
                MovementType.ISSUE,
                allocation.quantity,
                unit_cost=allocation.unit_cost,
                source_type="sales.Sale",
                source_id=str(sale.pk),
                source_reference=sale.sale_number,
                performed_by=actor,
                reason=f"Sale {sale.sale_number}",
            )
            SaleLineBatch.objects.create(
                sale_line=line,
                batch=allocation.batch,
                batch_number=allocation.batch.batch_number,
                expiry_date=allocation.batch.expiry_date,
                quantity=allocation.quantity,
                unit_cost=allocation.unit_cost,
            )
            line_cost = q_internal(line_cost + allocation.value)

        line.line_cost = line_cost
        line.unit_cost = q_internal(line_cost / line.quantity) if line.quantity else ZERO
        line.save(update_fields=["line_cost", "unit_cost"])

    # Any soft reservations this draft held are now realised.
    release_reservations(source_type="sales.Sale", source_id=str(sale.pk))

    _recalculate_sale(sale)
    sale.status = SaleStatus.CONFIRMED
    sale.confirmed_at = timezone.now()
    sale.updated_by = actor
    sale.save()

    # --- 3. Invoice -----------------------------------------------------
    invoice = None
    if generate_invoice:
        from apps.invoicing.services import create_invoice, post_invoice

        invoice_lines = []
        for line in lines:
            allocations = line.batch_allocations.all()
            invoice_lines.append(
                {
                    "product": line.product,
                    "product_code": line.product.product_code,
                    "description": line.product.display_name,
                    "batch_numbers": ", ".join(a.batch_number for a in allocations),
                    "expiry_dates": ", ".join(
                        a.expiry_date.strftime("%m/%Y") for a in allocations
                    ),
                    "quantity": line.quantity,
                    "unit_of_measure": line.product.unit_of_measure.code,
                    "unit_price": line.unit_price,
                    "discount_percent": line.discount_percent,
                    "tax_rate": line.tax_rate,
                    "unit_cost": line.unit_cost,
                }
            )

        invoice = create_invoice(
            customer=customer,
            lines=invoice_lines,
            is_credit_sale=(sale.sale_type == SaleType.CREDIT),
            sale=sale,
            invoice_date=sale.sale_date,
            reference=sale.customer_order_reference,
            actor=actor,
        )
        post_invoice(invoice, actor=actor)

    # --- 4. Customer activity + exposure --------------------------------
    if customer.first_sale_at is None:
        customer.first_sale_at = sale.sale_date
    customer.last_sale_at = sale.sale_date
    customer.save(update_fields=["first_sale_at", "last_sale_at", "updated_at"])

    if sale.sale_type == SaleType.CREDIT:
        from apps.partners.services import recompute_balance

        recompute_balance(customer)

    record(
        AuditAction.POST,
        Sale._meta.label,
        entity_id=str(sale.pk),
        entity_label=sale.sale_number,
        new_value={
            "status": sale.status,
            "total_amount": str(sale.total_amount),
            "total_cost": str(sale.total_cost),
            "invoice": invoice.invoice_number if invoice else None,
            "credit_override": bool(sale.credit_override_reason),
        },
        changed_fields=["status"],
        notes=(
            f"Sale confirmed: {sale.sale_number}"
            + (
                f" [CREDIT OVERRIDE by {credit_override_by}: {credit_override_reason}]"
                if sale.credit_override_reason
                else ""
            )
        ),
        actor=actor,
    )
    return sale, invoice


@transaction.atomic
def cancel_sale(sale: Sale, *, reason: str, actor=None) -> Sale:
    """
    Cancel a sale, returning any issued stock.

    A confirmed sale's stock is put back into the batches it came from, and
    its invoice is cancelled. Reversing the stock is what keeps the ledger
    honest â€” simply flagging the sale cancelled would leave phantom shortfalls.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to cancel a sale."), code="reason_required"
        )
    if sale.status in {SaleStatus.CANCELLED, SaleStatus.RETURNED}:
        raise InvalidStateTransition(
            _("This sale has already been cancelled or fully returned."),
            details={"status": sale.status},
        )

    previous_status = sale.status

    if sale.status != SaleStatus.DRAFT:
        for line in sale.lines.prefetch_related("batch_allocations"):
            for allocation in line.batch_allocations.all():
                outstanding = allocation.quantity - allocation.quantity_returned
                if outstanding <= ZERO:
                    continue
                from apps.inventory.models import StockBatch

                batch = StockBatch.objects.select_for_update().get(pk=allocation.batch_id)
                post_batch_movement(
                    batch,
                    MovementType.SALE_RETURN,
                    outstanding,
                    unit_cost=allocation.unit_cost,
                    source_type="sales.SaleCancellation",
                    source_id=str(sale.pk),
                    source_reference=sale.sale_number,
                    performed_by=actor,
                    reason=f"Sale cancelled: {reason}",
                )

        invoice = getattr(sale, "invoice", None)
        if invoice is not None:
            from apps.invoicing.models import InvoiceStatus
            from apps.invoicing.services import cancel_invoice

            if invoice.status != InvoiceStatus.CANCELLED:
                cancel_invoice(invoice, reason=f"Sale cancelled: {reason}", actor=actor)

    release_reservations(source_type="sales.Sale", source_id=str(sale.pk))

    sale.status = SaleStatus.CANCELLED
    sale.cancelled_at = timezone.now()
    sale.cancellation_reason = reason
    sale.updated_by = actor
    sale.save(
        update_fields=["status", "cancelled_at", "cancellation_reason", "updated_by", "updated_at"]
    )

    if sale.sale_type == SaleType.CREDIT:
        from apps.partners.services import recompute_balance

        recompute_balance(sale.customer)

    record(
        AuditAction.CANCEL,
        Sale._meta.label,
        entity_id=str(sale.pk),
        entity_label=sale.sale_number,
        previous_value={"status": previous_status},
        new_value={"status": SaleStatus.CANCELLED},
        changed_fields=["status"],
        notes=f"Sale cancelled: {reason}",
        actor=actor,
    )
    return sale


@transaction.atomic
def process_return(
    sale: Sale,
    *,
    lines: list[dict],
    reason: str,
    actor=None,
    issue_credit_note: bool = True,
) -> SaleReturn:
    """
    Process a customer return.

    Each line dict: sale_line, batch (optional), quantity, restock (bool),
    condition_notes.

    `restock` defaults to False and must be set deliberately. Returned
    medicines re-enter sellable stock only when cold chain and storage
    integrity were maintained; otherwise they are quarantined for destruction.
    Getting this wrong is a patient-safety failure, not an accounting one.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to process a return."), code="reason_required"
        )
    if sale.status in {SaleStatus.DRAFT, SaleStatus.CANCELLED}:
        raise InvalidStateTransition(
            _("Returns can only be processed against a confirmed sale."),
            details={"status": sale.status},
        )

    sale_return = SaleReturn.objects.create(
        return_number=next_number("sales.return"),
        sale=sale,
        customer=sale.customer,
        reason=reason,
        processed_by=actor,
        created_by=actor,
    )

    total_refund = ZERO
    credit_lines = []

    for data in lines:
        sale_line: SaleLine = data["sale_line"]
        quantity = q_internal(data["quantity"])
        restock = bool(data.get("restock", False))

        if quantity <= ZERO:
            raise BusinessRuleViolation(
                _("A returned quantity must be greater than zero."), code="invalid_quantity"
            )

        returnable = sale_line.quantity - sale_line.quantity_returned
        if quantity > returnable:
            raise BusinessRuleViolation(
                _("Cannot return %(qty)s of %(product)s: only %(max)s remain returnable on this line.")
                % {
                    "qty": quantity,
                    "product": sale_line.product.name,
                    "max": returnable,
                },
                code="return_exceeds_sold",
            )

        # Default to the batch this line was actually issued from, so returned
        # units go back to the correct expiry and cost.
        allocation = (
            sale_line.batch_allocations.filter(batch_id=data["batch"].pk).first()
            if data.get("batch")
            else sale_line.batch_allocations.first()
        )
        if allocation is None:
            raise BusinessRuleViolation(
                _("No batch allocation was found for this sale line."),
                code="missing_batch_allocation",
            )

        refund = q_internal(quantity * sale_line.unit_price)
        # Discount given at sale must be honoured on the refund, otherwise the
        # customer is refunded more than they paid.
        if sale_line.discount_percent:
            refund = q_internal(refund * (1 - sale_line.discount_percent / Decimal("100")))

        SaleReturnLine.objects.create(
            sale_return=sale_return,
            sale_line=sale_line,
            batch=allocation.batch,
            quantity=quantity,
            unit_price=sale_line.unit_price,
            refund_amount=refund,
            restock=restock,
            condition_notes=data.get("condition_notes", ""),
        )

        from apps.inventory.models import StockBatch

        batch = StockBatch.objects.select_for_update().get(pk=allocation.batch_id)

        if restock:
            post_batch_movement(
                batch,
                MovementType.SALE_RETURN,
                quantity,
                unit_cost=allocation.unit_cost,
                source_type="sales.SaleReturn",
                source_id=str(sale_return.pk),
                source_reference=sale_return.return_number,
                performed_by=actor,
                reason=f"Customer return: {reason}",
            )
        else:
            # Not restocked: the units come back but are immediately written
            # off, so the ledger shows both the return and the destruction.
            post_batch_movement(
                batch,
                MovementType.SALE_RETURN,
                quantity,
                unit_cost=allocation.unit_cost,
                source_type="sales.SaleReturn",
                source_id=str(sale_return.pk),
                source_reference=sale_return.return_number,
                performed_by=actor,
                reason=f"Customer return (quarantined): {reason}",
            )
            post_batch_movement(
                batch,
                MovementType.DAMAGE,
                quantity,
                unit_cost=allocation.unit_cost,
                source_type="sales.SaleReturn",
                source_id=str(sale_return.pk),
                source_reference=sale_return.return_number,
                performed_by=actor,
                reason=(
                    f"Returned stock not fit for resale: "
                    f"{data.get('condition_notes') or reason}"
                ),
            )

        sale_line.quantity_returned = q_internal(sale_line.quantity_returned + quantity)
        sale_line.save(update_fields=["quantity_returned"])

        allocation.quantity_returned = q_internal(allocation.quantity_returned + quantity)
        allocation.save(update_fields=["quantity_returned"])

        total_refund = q_internal(total_refund + refund)
        credit_lines.append(
            {
                "product": sale_line.product,
                "product_code": sale_line.product.product_code,
                "description": f"{sale_line.product.display_name} â€” {_('return')}",
                "batch_numbers": allocation.batch_number,
                "quantity": quantity,
                "unit_price": sale_line.unit_price,
                "discount_percent": sale_line.discount_percent,
                "tax_rate": sale_line.tax_rate,
                "unit_cost": allocation.unit_cost,
            }
        )

    sale_return.total_amount = total_refund
    sale_return.save(update_fields=["total_amount", "updated_at"])

    # Update the sale's return status.
    all_lines = list(sale.lines.all())
    fully_returned = all(line.quantity_returned >= line.quantity for line in all_lines)
    any_returned = any(line.quantity_returned > ZERO for line in all_lines)
    if fully_returned:
        sale.status = SaleStatus.RETURNED
    elif any_returned:
        sale.status = SaleStatus.PARTIALLY_RETURNED
    sale.save(update_fields=["status", "updated_at"])

    if issue_credit_note and credit_lines:
        invoice = getattr(sale, "invoice", None)
        if invoice is not None:
            from apps.invoicing.services import issue_credit_note as _issue

            note = _issue(invoice, lines=credit_lines, reason=reason, actor=actor)
            sale_return.credit_note = note
            sale_return.save(update_fields=["credit_note", "updated_at"])

    record(
        AuditAction.UPDATE,
        SaleReturn._meta.label,
        entity_id=str(sale_return.pk),
        entity_label=sale_return.return_number,
        new_value={
            "sale": sale.sale_number,
            "total_refund": str(total_refund),
            "lines": len(credit_lines),
        },
        notes=f"Return processed: {reason}",
        actor=actor,
    )
    return sale_return


def trace_batch_recipients(batch_number: str) -> list[dict]:
    """
    Every customer who received units from a batch.

    The recall query. Ordered by sale date so the most recent shipments â€”
    the ones most likely still on a pharmacy's shelf â€” appear first.
    """
    allocations = (
        SaleLineBatch.objects.filter(batch_number=batch_number)
        .select_related(
            "sale_line__sale__customer", "sale_line__product", "sale_line__sale",
        )
        .order_by("-sale_line__sale__sale_date")
    )

    return [
        {
            "sale_number": a.sale_line.sale.sale_number,
            "sale_date": a.sale_line.sale.sale_date,
            "customer_code": a.sale_line.sale.customer.customer_code,
            "customer_name": a.sale_line.sale.customer.business_name,
            "customer_phone": a.sale_line.sale.customer.phone,
            "customer_email": a.sale_line.sale.customer.email,
            "product": a.sale_line.product.display_name,
            "batch_number": a.batch_number,
            "expiry_date": a.expiry_date,
            "quantity_supplied": a.quantity,
            "quantity_returned": a.quantity_returned,
            "quantity_outstanding": a.quantity - a.quantity_returned,
        }
        for a in allocations
    ]

