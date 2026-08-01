"""
Invoicing services.

The central rule: a posted invoice is immutable. Posting is the moment a
document becomes fiscal, and from then on the only lawful corrections are
cancellation (with reason) or a credit note. Every function here enforces
that boundary rather than trusting callers.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record, snapshot
from apps.core.exceptions import (
    BusinessRuleViolation,
    DocumentLocked,
    InvalidStateTransition,
)
from apps.core.models import AuditAction
from apps.core.money import compute_line, q_internal
from apps.core.numbering import next_number

from .models import (
    OPEN_STATUSES,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    InvoiceType,
    Payment,
    PaymentAllocation,
    PaymentMethod,
)

ZERO = Decimal("0")


def _recalculate_totals(invoice: Invoice) -> Invoice:
    """
    Recompute header totals from the lines.

    Totals are derived from the *stored, already-rounded* line amounts rather
    than recomputed from quantity × price. That is what guarantees the printed
    invoice foots: an auditor adding the visible line totals must reach the
    visible grand total, with no hidden fractional remainder.
    """
    aggregate = invoice.lines.aggregate(
        subtotal=Sum("line_subtotal"),
        discount=Sum("discount_amount"),
        tax=Sum("tax_amount"),
        total=Sum("line_total"),
    )

    invoice.subtotal = q_internal(aggregate["subtotal"] or ZERO)
    invoice.discount_amount = q_internal(aggregate["discount"] or ZERO)
    invoice.tax_amount = q_internal(aggregate["tax"] or ZERO)
    invoice.taxable_amount = q_internal(invoice.subtotal - invoice.discount_amount)
    invoice.total_amount = q_internal(aggregate["total"] or ZERO)
    invoice.balance_due = q_internal(invoice.total_amount - invoice.paid_amount)
    return invoice


@transaction.atomic
def create_invoice(
    *,
    customer,
    lines: list[dict],
    invoice_type: str = InvoiceType.STANDARD,
    is_credit_sale: bool = False,
    sale=None,
    invoice_date=None,
    reference: str = "",
    notes: str = "",
    original_invoice=None,
    actor=None,
) -> Invoice:
    """
    Create a DRAFT invoice.

    Each line dict: product, description, quantity, unit_price,
    discount_percent, tax_rate, batch_numbers, unit_cost.

    `original_invoice` must be supplied when creating a credit note: the
    `credit_note_requires_original` CHECK constraint enforces the link at
    insert time, so it cannot be attached in a later UPDATE.

    The number is allocated here, inside the caller's transaction, so a
    rollback releases it and the fiscal series stays gap-free.
    """
    if not lines:
        raise BusinessRuleViolation(
            _("An invoice must contain at least one line."), code="empty_invoice"
        )

    invoice_date = invoice_date or timezone.now()

    sequence_key = {
        InvoiceType.CREDIT_NOTE: "sales.credit_note",
        InvoiceType.DEBIT_NOTE: "sales.debit_note",
    }.get(invoice_type, "sales.invoice")

    due_date = None
    terms_days = 0
    if is_credit_sale:
        terms_days = customer.payment_term_days
        due_date = (invoice_date + timezone.timedelta(days=terms_days)).date()

    invoice = Invoice.objects.create(
        invoice_number=next_number(sequence_key, when=invoice_date),
        invoice_type=invoice_type,
        status=InvoiceStatus.DRAFT,
        customer=customer,
        sale=sale,
        # Frozen customer identity — see the model docstring.
        customer_name=customer.business_name,
        customer_nif=customer.nif,
        customer_address=customer.address,
        customer_phone=customer.phone,
        invoice_date=invoice_date,
        due_date=due_date,
        payment_terms_days=terms_days,
        is_credit_sale=is_credit_sale,
        reference=reference,
        notes=notes,
        original_invoice=original_invoice,
        created_by=actor,
    )

    for index, data in enumerate(lines, start=1):
        _create_line(invoice, index, data)

    _recalculate_totals(invoice)
    invoice.save()

    record(
        AuditAction.CREATE,
        Invoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.invoice_number,
        new_value=snapshot(invoice),
        actor=actor,
    )
    return invoice


def _create_line(invoice: Invoice, line_number: int, data: dict) -> InvoiceLine:
    """Build one line, computing its amounts through the shared money helper."""
    quantity = q_internal(data["quantity"])
    unit_price = q_internal(data["unit_price"])
    discount_percent = q_internal(data.get("discount_percent", ZERO))
    tax_rate = q_internal(data.get("tax_rate", ZERO))

    if quantity <= ZERO:
        raise BusinessRuleViolation(
            _("Line quantities must be greater than zero."), code="invalid_quantity"
        )
    if unit_price < ZERO:
        raise BusinessRuleViolation(
            _("Unit prices cannot be negative."), code="invalid_price"
        )
    if discount_percent < ZERO or discount_percent > 100:
        raise BusinessRuleViolation(
            _("A discount must be between 0 and 100 percent."), code="invalid_discount"
        )

    amounts = compute_line(quantity, unit_price, discount_percent, tax_rate)
    product = data.get("product")

    return InvoiceLine.objects.create(
        invoice=invoice,
        line_number=line_number,
        product=product,
        product_code=data.get("product_code") or (product.product_code if product else ""),
        description=data.get("description") or (product.display_name if product else ""),
        batch_numbers=data.get("batch_numbers", ""),
        expiry_dates=data.get("expiry_dates", ""),
        quantity=quantity,
        unit_of_measure=data.get("unit_of_measure", ""),
        unit_price=unit_price,
        discount_percent=discount_percent,
        discount_amount=amounts["discount"],
        tax_rate=tax_rate,
        tax_amount=amounts["tax"],
        line_subtotal=amounts["gross"],
        line_total=amounts["total"],
        unit_cost=q_internal(data.get("unit_cost", ZERO)),
    )


@transaction.atomic
def post_invoice(invoice: Invoice, *, actor=None) -> Invoice:
    """
    Post a draft, making it a fiscal document.

    After this the invoice is locked: `update_invoice` will refuse it, and
    corrections must go through cancellation or a credit note.
    """
    if invoice.status != InvoiceStatus.DRAFT:
        raise InvalidStateTransition(
            _("Only a draft invoice can be posted; this one is %(status)s.")
            % {"status": invoice.get_status_display()},
            details={"current_status": invoice.status},
        )
    if not invoice.lines.exists():
        raise BusinessRuleViolation(
            _("An invoice cannot be posted without lines."), code="empty_invoice"
        )

    # A credit sale to an unidentified customer cannot be filed with the OBR.
    if invoice.is_credit_sale and not invoice.customer_nif:
        raise BusinessRuleViolation(
            _("A NIF is required to post a credit invoice."), code="nif_required"
        )

    _recalculate_totals(invoice)
    invoice.status = InvoiceStatus.POSTED
    invoice.posted_at = timezone.now()
    invoice.posted_by = actor
    invoice.save()

    # The customer's exposure changes the moment the invoice becomes real.
    if invoice.is_credit_sale:
        from apps.partners.services import recompute_balance

        recompute_balance(invoice.customer)

    record(
        AuditAction.POST,
        Invoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.invoice_number,
        new_value={
            "status": invoice.status,
            "total_amount": str(invoice.total_amount),
            "balance_due": str(invoice.balance_due),
        },
        changed_fields=["status"],
        notes=f"Invoice posted: {invoice.invoice_number}",
        actor=actor,
    )
    return invoice


@transaction.atomic
def update_invoice(invoice: Invoice, *, lines: list[dict] | None = None, actor=None, **data) -> Invoice:
    """Modify a draft. Refuses anything already posted."""
    if not invoice.is_editable:
        raise DocumentLocked(
            _("Invoice %(number)s is %(status)s and can no longer be modified. Issue a credit note instead.")
            % {"number": invoice.invoice_number, "status": invoice.get_status_display()},
            details={"invoice_number": invoice.invoice_number, "status": invoice.status},
        )

    before = snapshot(invoice)

    for field, value in data.items():
        setattr(invoice, field, value)

    if lines is not None:
        invoice.lines.all().delete()
        for index, line_data in enumerate(lines, start=1):
            _create_line(invoice, index, line_data)

    _recalculate_totals(invoice)
    invoice.updated_by = actor
    invoice.save()

    record(
        AuditAction.UPDATE,
        Invoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.invoice_number,
        previous_value=before,
        new_value=snapshot(invoice),
        actor=actor,
    )
    return invoice


@transaction.atomic
def cancel_invoice(invoice: Invoice, *, reason: str, actor=None) -> Invoice:
    """
    Cancel an invoice.

    The row is retained with CANCELLED status rather than deleted — a missing
    number in a fiscal series is read as a suppressed sale. Payments must be
    reversed first: cancelling an invoice that has been paid would leave the
    customer's money unattached to any document.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to cancel an invoice."), code="reason_required"
        )
    if invoice.status == InvoiceStatus.CANCELLED:
        raise InvalidStateTransition(
            _("This invoice has already been cancelled."), details={"status": invoice.status}
        )
    if invoice.paid_amount > ZERO:
        raise BusinessRuleViolation(
            _("This invoice has received payments totalling %(paid)s BIF. Reverse them before cancelling.")
            % {"paid": invoice.paid_amount},
            code="invoice_has_payments",
        )

    previous_status = invoice.status
    invoice.status = InvoiceStatus.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.cancelled_by = actor
    invoice.cancellation_reason = reason
    invoice.balance_due = ZERO
    invoice.save()

    if invoice.is_credit_sale:
        from apps.partners.services import recompute_balance

        recompute_balance(invoice.customer)

    record(
        AuditAction.CANCEL,
        Invoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.invoice_number,
        previous_value={"status": previous_status},
        new_value={"status": InvoiceStatus.CANCELLED},
        changed_fields=["status"],
        notes=f"Invoice cancelled: {reason}",
        actor=actor,
    )
    return invoice


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


@transaction.atomic
def record_payment(
    *,
    customer,
    amount: Decimal,
    method: str = PaymentMethod.CASH,
    payment_date=None,
    bank_reference: str = "",
    notes: str = "",
    invoice_ids: list | None = None,
    actor=None,
) -> Payment:
    """
    Record a payment and allocate it to open invoices.

    When no specific invoices are named, allocation is oldest-due-first. That
    is the standard commercial convention and the one that minimises the
    customer's ageing profile — important because ageing drives credit
    decisions and collection escalation.
    """
    amount = q_internal(amount)
    if amount <= ZERO:
        raise BusinessRuleViolation(
            _("A payment amount must be greater than zero."), code="invalid_amount"
        )

    payment_date = payment_date or timezone.now()

    payment = Payment.objects.create(
        reference=next_number("sales.receipt", when=payment_date),
        customer=customer,
        payment_date=payment_date,
        amount=amount,
        method=method,
        bank_reference=bank_reference,
        notes=notes,
        received_by=actor,
        created_by=actor,
    )

    open_invoices = Invoice.objects.select_for_update().filter(
        customer=customer, status__in=OPEN_STATUSES,
        balance_due__gt=ZERO, deleted_at__isnull=True,
    )
    if invoice_ids:
        open_invoices = open_invoices.filter(id__in=invoice_ids)
    # Oldest due date first; invoices with no due date settle last.
    open_invoices = open_invoices.order_by(F("due_date").asc(nulls_last=True), "invoice_date")

    remaining = amount
    for invoice in open_invoices:
        if remaining <= ZERO:
            break
        applied = min(remaining, invoice.balance_due)
        _apply_to_invoice(payment, invoice, applied, actor=actor)
        remaining = q_internal(remaining - applied)

    payment.allocated_amount = q_internal(amount - remaining)
    payment.save(update_fields=["allocated_amount", "updated_at"])

    from apps.partners.services import recompute_balance

    recompute_balance(customer)

    record(
        AuditAction.CREATE,
        Payment._meta.label,
        entity_id=str(payment.pk),
        entity_label=payment.reference,
        new_value={
            "amount": str(amount),
            "allocated": str(payment.allocated_amount),
            "unallocated": str(remaining),
            "method": method,
            "customer": customer.customer_code,
        },
        notes=f"Payment received: {payment.reference}",
        actor=actor,
    )
    return payment


def _apply_to_invoice(payment: Payment, invoice: Invoice, amount: Decimal, *, actor=None) -> None:
    """Allocate part of a payment to one invoice and refresh its status."""
    PaymentAllocation.objects.create(
        payment=payment, invoice=invoice, amount=amount, created_by=actor,
    )

    invoice.paid_amount = q_internal(invoice.paid_amount + amount)
    invoice.balance_due = q_internal(invoice.total_amount - invoice.paid_amount)

    if invoice.balance_due <= ZERO:
        invoice.status = InvoiceStatus.PAID
        invoice.balance_due = ZERO
    elif invoice.paid_amount > ZERO:
        invoice.status = InvoiceStatus.PARTIALLY_PAID

    invoice.save(update_fields=["paid_amount", "balance_due", "status", "updated_at"])


@transaction.atomic
def reverse_payment(payment: Payment, *, reason: str, actor=None) -> Payment:
    """
    Reverse a payment (bounced cheque, erroneous entry).

    The payment row and its allocations are retained; the invoices are
    restored to their prior open state. Deleting the payment would destroy
    the record that money arrived and was later withdrawn — which is exactly
    what a bounced cheque investigation needs to see.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to reverse a payment."), code="reason_required"
        )
    if payment.is_reversed:
        raise InvalidStateTransition(_("This payment has already been reversed."))

    for allocation in payment.allocations.select_related("invoice"):
        invoice = Invoice.objects.select_for_update().get(pk=allocation.invoice_id)
        invoice.paid_amount = q_internal(max(ZERO, invoice.paid_amount - allocation.amount))
        invoice.balance_due = q_internal(invoice.total_amount - invoice.paid_amount)
        if invoice.paid_amount <= ZERO:
            invoice.status = InvoiceStatus.POSTED
        elif invoice.balance_due > ZERO:
            invoice.status = InvoiceStatus.PARTIALLY_PAID
        invoice.save(update_fields=["paid_amount", "balance_due", "status", "updated_at"])

    payment.is_reversed = True
    payment.reversal_reason = reason
    payment.allocated_amount = ZERO
    payment.save(update_fields=["is_reversed", "reversal_reason", "allocated_amount", "updated_at"])

    from apps.partners.services import recompute_balance

    recompute_balance(payment.customer)

    record(
        AuditAction.CANCEL,
        Payment._meta.label,
        entity_id=str(payment.pk),
        entity_label=payment.reference,
        new_value={"is_reversed": True},
        changed_fields=["is_reversed"],
        notes=f"Payment reversed: {reason}",
        actor=actor,
    )
    return payment


# ---------------------------------------------------------------------------
# Credit / debit notes
# ---------------------------------------------------------------------------


@transaction.atomic
def issue_credit_note(
    original: Invoice, *, lines: list[dict], reason: str, actor=None
) -> Invoice:
    """
    Issue a credit note against a posted invoice.

    This is the *only* lawful way to reduce an already-issued invoice. The
    note is a separate fiscal document in its own series, linked to the
    original so the pair reconciles.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to issue a credit note."), code="reason_required"
        )
    if original.status in {InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED}:
        raise InvalidStateTransition(
            _("A credit note can only be issued against a posted invoice."),
            details={"status": original.status},
        )

    note = create_invoice(
        customer=original.customer,
        lines=lines,
        invoice_type=InvoiceType.CREDIT_NOTE,
        is_credit_sale=original.is_credit_sale,
        reference=original.invoice_number,
        notes=reason,
        original_invoice=original,
        actor=actor,
    )
    post_invoice(note, actor=actor)

    # Offset the original: the credit reduces what the customer owes.
    credited = min(note.total_amount, original.balance_due)
    if credited > ZERO:
        original.paid_amount = q_internal(original.paid_amount + credited)
        original.balance_due = q_internal(original.total_amount - original.paid_amount)
        original.status = (
            InvoiceStatus.PAID if original.balance_due <= ZERO else InvoiceStatus.PARTIALLY_PAID
        )
        original.save(update_fields=["paid_amount", "balance_due", "status", "updated_at"])

    from apps.partners.services import recompute_balance

    recompute_balance(original.customer)

    record(
        AuditAction.CREATE,
        Invoice._meta.label,
        entity_id=str(note.pk),
        entity_label=note.invoice_number,
        new_value={
            "type": InvoiceType.CREDIT_NOTE,
            "original_invoice": original.invoice_number,
            "amount": str(note.total_amount),
            "credited_against_original": str(credited),
        },
        notes=f"Credit note {note.invoice_number} issued against {original.invoice_number}: {reason}",
        actor=actor,
    )
    return note


@transaction.atomic
def mark_overdue_invoices() -> int:
    """
    Flag posted invoices past their due date.

    Run daily. Kept as a status transition rather than a computed property so
    the ageing report and dashboard can filter on an indexed column instead of
    evaluating dates across the whole table.
    """
    today = timezone.localdate()
    overdue = Invoice.objects.filter(
        status__in=[InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID],
        due_date__lt=today,
        balance_due__gt=ZERO,
        deleted_at__isnull=True,
    )
    count = overdue.count()
    if count:
        overdue.update(status=InvoiceStatus.OVERDUE, updated_at=timezone.now())
    return count


def register_print(invoice: Invoice, *, actor=None) -> Invoice:
    """
    Record that a fiscal document was printed.

    Reprints are counted so a duplicate can be marked as such on the PDF —
    an unmarked second original is a fraud vector.
    """
    invoice.print_count = F("print_count") + 1
    invoice.last_printed_at = timezone.now()
    invoice.save(update_fields=["print_count", "last_printed_at"])
    invoice.refresh_from_db(fields=["print_count"])

    record(
        AuditAction.PRINT,
        Invoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.invoice_number,
        notes=f"Document printed (copy #{invoice.print_count})",
        actor=actor,
    )
    return invoice
