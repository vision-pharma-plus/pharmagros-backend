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
    GOODS_RETURN_REASONS,
    OPEN_STATUSES,
    CreditNoteReason,
    FiscalStatus,
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
        # Falls back to the product's own unit for the same reason as
        # `product_code` above: the printed invoice gives this column of its
        # own, and a caller that omits it — the invoice API, or the sale
        # return path building a credit note — would otherwise leave a blank
        # cell on a document that had the value available all along.
        unit_of_measure=(
            data.get("unit_of_measure")
            or (product.unit_of_measure.code if product else "")
        ),
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

    # Assign the fiscal identity now, while the document is becoming real.
    # The signature is computed locally, so this adds no network dependency
    # to posting: a counter sale completes whether or not the OBR is
    # reachable. Actual declaration is drained asynchronously by the sweep.
    from .fiscal import service as fiscal

    if fiscal.is_enabled() and fiscal.is_declarable(invoice):
        fiscal.assign_signature(invoice)
        invoice.fiscal_status = FiscalStatus.PENDING

    invoice.save()

    # Money the customer has already handed over settles this invoice
    # immediately. Credit notes are excluded: a note is itself a reduction of
    # what is owed, not a receipt to be spent against other documents.
    if invoice.invoice_type != InvoiceType.CREDIT_NOTE:
        apply_available_credit(invoice, actor=actor)

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
    # Read the fiscal status from the database rather than from this
    # instance: declaration happens asynchronously, so a caller holding an
    # Invoice loaded before the sweep ran would otherwise see a stale
    # PENDING and wrongly drop a document the OBR has already accepted.
    previous_fiscal_status = (
        Invoice.objects.filter(pk=invoice.pk)
        .values_list("fiscal_status", flat=True)
        .first()
    )
    invoice.fiscal_status = previous_fiscal_status
    invoice.status = InvoiceStatus.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.cancelled_by = actor
    invoice.cancellation_reason = reason
    invoice.balance_due = ZERO

    # A document still queued locally has never reached the OBR, so there is
    # nothing to withdraw — drop it from the queue. One already declared must
    # be withdrawn over the network, which the sweep does asynchronously so
    # cancelling never blocks on a link being up.
    if previous_fiscal_status == FiscalStatus.PENDING:
        invoice.fiscal_status = FiscalStatus.NOT_REQUIRED

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


def _settleable_invoices(customer, invoice_ids: list | None = None):
    """
    Open invoices for a customer, locked, in the order money should settle them.

    Oldest due date first; invoices with no due date settle last. That is the
    standard commercial convention and the one that minimises the customer's
    ageing profile — important because ageing drives credit decisions and
    collection escalation.

    Rows are locked here rather than read and re-fetched later: every caller
    goes on to mutate `paid_amount`, and two payments landing on one invoice
    concurrently would otherwise each compute a new balance from the same
    stale figure and lose one of the two.
    """
    queryset = Invoice.objects.select_for_update().filter(
        customer=customer,
        status__in=OPEN_STATUSES,
        balance_due__gt=ZERO,
        deleted_at__isnull=True,
    )
    if invoice_ids:
        queryset = queryset.filter(id__in=invoice_ids)
    return queryset.order_by(F("due_date").asc(nulls_last=True), "invoice_date")


def _allocate(payment: Payment, invoices, remaining: Decimal, *, actor=None) -> Decimal:
    """
    Spread `remaining` across `invoices`, returning what could not be placed.

    Shared by every path that puts money against invoices — a new payment, a
    credit note offset, and the drawdown of an existing credit when a fresh
    invoice is posted — so all three produce the same allocation rows and the
    same status transitions.
    """
    for invoice in invoices:
        if remaining <= ZERO:
            break
        applied = min(remaining, invoice.balance_due)
        if applied <= ZERO:
            continue
        _apply_to_invoice(payment, invoice, applied, actor=actor)
        remaining = q_internal(remaining - applied)
    return remaining


def _sync_allocated_amount(payment: Payment) -> Decimal:
    """
    Reset a payment's allocated total to the sum of its live allocation rows.

    Derived rather than incremented, for the same reason as the customer
    balance: allocations are added by several paths and removed on reversal,
    and a running tally would drift out of agreement with the rows that
    justify it. `unallocated_amount` is read to decide whether a customer has
    money in hand, so drift here spends money twice.
    """
    total = q_internal(
        payment.allocations.aggregate(total=Sum("amount"))["total"] or ZERO
    )
    if payment.allocated_amount != total:
        payment.allocated_amount = total
        payment.save(update_fields=["allocated_amount", "updated_at"])
    return total


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

    When no specific invoices are named, allocation is oldest-due-first.

    Naming invoices that cannot take the money is refused rather than quietly
    ignored: silently redirecting a payment the cashier aimed at a particular
    invoice produces a correct-looking receipt that settles the wrong debt,
    and nobody discovers it until the customer disputes their statement.

    Anything left over after the open invoices are settled stays on the
    payment as unallocated credit and is drawn down automatically by
    `post_invoice`.
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

    open_invoices = list(_settleable_invoices(customer, invoice_ids))

    if invoice_ids:
        _reject_unsettleable(customer, invoice_ids, open_invoices)

    remaining = _allocate(payment, open_invoices, amount, actor=actor)
    _sync_allocated_amount(payment)

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


def _reject_unsettleable(customer, invoice_ids: list, settleable: list) -> None:
    """
    Refuse a payment naming an invoice that cannot receive it.

    Distinguishes the three reasons so the cashier is told which invoice is
    wrong and why, rather than being handed a generic failure on a screen
    that has already taken the customer's cash.
    """
    settleable_ids = {invoice.pk for invoice in settleable}
    unsettleable = [str(pk) for pk in invoice_ids if pk not in settleable_ids]
    if not unsettleable:
        return

    known = {
        invoice.pk: invoice
        for invoice in Invoice.objects.filter(
            id__in=unsettleable, deleted_at__isnull=True
        )
    }

    problems = []
    for raw_id in unsettleable:
        invoice = next(
            (inv for pk, inv in known.items() if str(pk) == raw_id), None
        )
        if invoice is None:
            problems.append(_("%(id)s: no such invoice.") % {"id": raw_id})
        elif invoice.customer_id != customer.pk:
            problems.append(
                _("%(number)s belongs to another customer.")
                % {"number": invoice.invoice_number}
            )
        elif invoice.status not in OPEN_STATUSES:
            problems.append(
                _("%(number)s is %(status)s and cannot take a payment.")
                % {
                    "number": invoice.invoice_number,
                    "status": invoice.get_status_display(),
                }
            )
        else:
            problems.append(
                _("%(number)s is already settled in full.")
                % {"number": invoice.invoice_number}
            )

    raise BusinessRuleViolation(
        _("This payment cannot be applied as requested. %(problems)s")
        % {"problems": " ".join(problems)},
        code="invoice_not_settleable",
        details={"invoices": unsettleable},
    )


def _apply_to_invoice(payment: Payment, invoice: Invoice, amount: Decimal, *, actor=None) -> None:
    """
    Allocate part of a payment to one invoice and refresh its status.

    The allocation row is created with `update_or_create` because one payment
    may reach the same invoice twice — a credit drawn down in two passes, or a
    caller re-running a partially completed allocation — and the
    `unique_payment_invoice_allocation` constraint makes a blind insert fail
    there. The pair stays unique and the amounts accumulate.
    """
    allocation, created = PaymentAllocation.objects.get_or_create(
        payment=payment, invoice=invoice,
        defaults={"amount": amount, "created_by": actor},
    )
    if not created:
        allocation.amount = q_internal(allocation.amount + amount)
        allocation.save(update_fields=["amount"])

    invoice.paid_amount = q_internal(invoice.paid_amount + amount)
    invoice.balance_due = q_internal(invoice.total_amount - invoice.paid_amount)

    if invoice.balance_due <= ZERO:
        invoice.status = InvoiceStatus.PAID
        invoice.balance_due = ZERO
    elif invoice.paid_amount > ZERO:
        invoice.status = InvoiceStatus.PARTIALLY_PAID

    invoice.save(update_fields=["paid_amount", "balance_due", "status", "updated_at"])


@transaction.atomic
def apply_available_credit(invoice: Invoice, *, actor=None) -> Decimal:
    """
    Draw down a customer's unallocated payments against a newly open invoice.

    Money already in hand must settle the next debt without anyone
    remembering to do it. Without this, an overpayment or a payment taken
    before its invoice existed sits unallocated forever while the same
    customer is chased for the balance it should already have cleared.

    Oldest payment first, so the credit that has been held longest is the one
    that clears. Returns the amount applied.
    """
    if invoice.status not in OPEN_STATUSES or invoice.balance_due <= ZERO:
        return ZERO

    credits = (
        Payment.objects.select_for_update()
        .filter(
            customer_id=invoice.customer_id,
            is_reversed=False,
            deleted_at__isnull=True,
            allocated_amount__lt=F("amount"),
        )
        .order_by("payment_date", "reference")
    )

    applied_total = ZERO
    for payment in credits:
        if invoice.balance_due <= ZERO:
            break
        available = q_internal(payment.amount - payment.allocated_amount)
        if available <= ZERO:
            continue
        applied = min(available, invoice.balance_due)
        _apply_to_invoice(payment, invoice, applied, actor=actor)
        _sync_allocated_amount(payment)
        applied_total = q_internal(applied_total + applied)

        record(
            AuditAction.UPDATE,
            Payment._meta.label,
            entity_id=str(payment.pk),
            entity_label=payment.reference,
            new_value={
                "allocated_to": invoice.invoice_number,
                "amount": str(applied),
                "unallocated_after": str(payment.unallocated_amount),
            },
            changed_fields=["allocated_amount"],
            notes=(
                f"Credit of {applied} BIF applied automatically to "
                f"{invoice.invoice_number}"
            ),
            actor=actor,
        )

    return applied_total


@transaction.atomic
def allocate_payment(
    payment: Payment, *, invoice_ids: list | None = None, actor=None
) -> Payment:
    """
    Place a payment's unallocated remainder against the customer's open invoices.

    The manual counterpart to the automatic drawdown in `post_invoice`, for
    money that arrived before there was anything to settle, or that a clerk
    wants directed at particular invoices rather than the oldest.

    Refuses a reversed payment: that money has been withdrawn and is no
    longer the customer's to spend.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    if payment.is_reversed:
        raise InvalidStateTransition(
            _("This payment has been reversed and cannot be allocated."),
            details={"reference": payment.reference},
        )

    remaining = q_internal(payment.amount - payment.allocated_amount)
    if remaining <= ZERO:
        raise BusinessRuleViolation(
            _("Payment %(reference)s is already fully allocated.")
            % {"reference": payment.reference},
            code="nothing_to_allocate",
        )

    open_invoices = list(_settleable_invoices(payment.customer, invoice_ids))
    if invoice_ids:
        _reject_unsettleable(payment.customer, invoice_ids, open_invoices)

    if not open_invoices:
        raise BusinessRuleViolation(
            _("%(customer)s has no open invoices to allocate this payment to.")
            % {"customer": payment.customer.business_name},
            code="no_open_invoices",
        )

    left = _allocate(payment, open_invoices, remaining, actor=actor)
    applied = q_internal(remaining - left)
    _sync_allocated_amount(payment)

    from apps.partners.services import recompute_balance

    recompute_balance(payment.customer)

    record(
        AuditAction.UPDATE,
        Payment._meta.label,
        entity_id=str(payment.pk),
        entity_label=payment.reference,
        new_value={
            "applied": str(applied),
            "unallocated_after": str(left),
        },
        changed_fields=["allocated_amount"],
        notes=f"Payment {payment.reference} allocated: {applied} BIF applied",
        actor=actor,
    )
    return payment


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

    # Re-read under lock. Without this, two clerks reversing the same bounced
    # cheque both pass the check above and each unwind the invoices, taking
    # the balance back twice as far as the payment ever advanced it.
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.is_reversed:
        raise InvalidStateTransition(_("This payment has already been reversed."))

    for allocation in payment.allocations.select_related("invoice"):
        invoice = Invoice.objects.select_for_update().get(pk=allocation.invoice_id)
        invoice.paid_amount = q_internal(max(ZERO, invoice.paid_amount - allocation.amount))
        invoice.balance_due = q_internal(invoice.total_amount - invoice.paid_amount)
        # A cancelled invoice keeps its status: the money coming back out does
        # not reopen a document that has been withdrawn.
        if invoice.status != InvoiceStatus.CANCELLED:
            if invoice.paid_amount <= ZERO:
                invoice.status = InvoiceStatus.POSTED
            elif invoice.balance_due > ZERO:
                invoice.status = InvoiceStatus.PARTIALLY_PAID
            # Restore the ageing the payment had cleared. Leaving it POSTED
            # would hide a past-due debt from the ageing report and the
            # collection list until the next nightly sweep — precisely the
            # window in which a bounced cheque needs chasing.
            if invoice.balance_due > ZERO and invoice.is_overdue:
                invoice.status = InvoiceStatus.OVERDUE
        invoice.save(update_fields=["paid_amount", "balance_due", "status", "updated_at"])

    # The rows go, not just the total. They are what `unallocated_amount` and
    # the invoice's payment history are derived from, so a retained row would
    # show withdrawn money as still settling the invoice while simultaneously
    # counting it as credit available to spend elsewhere.
    payment.allocations.all().delete()

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
    original: Invoice,
    *,
    lines: list[dict],
    reason: str,
    reason_code: str = CreditNoteReason.OTHER,
    actor=None,
) -> Invoice:
    """
    Issue a credit note against a posted invoice.

    This is the *only* lawful way to reduce an already-issued invoice. The
    note is a separate fiscal document in its own series, linked to the
    original so the pair reconciles.

    Purely financial: crediting the customer never moves stock. When goods
    have physically come back, `issue_credit_note_for_invoice` routes through
    the sale return path instead, which is what knows the batches involved.
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
    # A credit note against a credit note has no meaning: the correction of a
    # correction is a fresh invoice or debit note, not another credit.
    if original.invoice_type == InvoiceType.CREDIT_NOTE:
        raise BusinessRuleViolation(
            _("A credit note cannot itself be credited."),
            code="cannot_credit_a_credit_note",
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
    note.credit_reason_code = reason_code
    note.save(update_fields=["credit_reason_code"])
    post_invoice(note, actor=actor)

    # Offset the original: the credit reduces what the customer owes.
    #
    # Routed through a Payment carrying method=CREDIT_NOTE rather than written
    # straight onto `paid_amount`, so that an invoice's paid total always
    # equals the sum of its allocation rows. A bare increment made the two
    # disagree, leaving an invoice marked PAID with no record of what settled
    # it — unreconcilable against a customer statement, and invisible to the
    # reversal path, which only knows how to unwind allocations.
    credited = ZERO
    if original.balance_due > ZERO and note.total_amount > ZERO:
        offset = Payment.objects.create(
            reference=next_number("sales.receipt", when=note.invoice_date),
            customer=original.customer,
            payment_date=note.invoice_date,
            amount=min(note.total_amount, original.balance_due),
            method=PaymentMethod.CREDIT_NOTE,
            bank_reference=note.invoice_number,
            notes=_("Credit note %(number)s applied to %(original)s")
            % {"number": note.invoice_number, "original": original.invoice_number},
            received_by=actor,
            created_by=actor,
        )
        # Lock the original before touching its balance: it may be taking a
        # cash payment on another connection at this moment.
        locked = Invoice.objects.select_for_update().get(pk=original.pk)
        credited = min(offset.amount, locked.balance_due)
        if credited > ZERO:
            _apply_to_invoice(offset, locked, credited, actor=actor)
            _sync_allocated_amount(offset)
            # Keep the caller's instance in step with what was just written.
            original.paid_amount = locked.paid_amount
            original.balance_due = locked.balance_due
            original.status = locked.status

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
            "reason_code": reason_code,
        },
        notes=f"Credit note {note.invoice_number} issued against {original.invoice_number}: {reason}",
        actor=actor,
    )
    return note


@transaction.atomic
def issue_credit_note_for_invoice(
    original: Invoice,
    *,
    lines: list[dict],
    reason: str,
    reason_code: str = CreditNoteReason.OTHER,
    actor=None,
) -> Invoice:
    """
    Issue a credit note against an invoice, reconciling stock when goods came back.

    The entry point behind the invoice's "Issue credit note" action. It picks
    one of two paths:

    * Goods physically returned, and the invoice came from a sale — delegate
      to `sales.process_return`, which restocks each line into the batch it
      was actually issued from, quarantines what is unfit for resale, and
      raises the credit note itself. Restocking has to happen there because
      the batch allocations hang off the sale line, not the invoice line: an
      invoice only carries batch numbers as printed text.

    * Anything else — a pricing or quantity correction, a post-hoc discount,
      or a returned-goods note on an invoice with no sale behind it — is a
      purely financial correction and goes straight to `issue_credit_note`.

    Lines carry `sale_line` and `restock` only in the first case; the
    financial path ignores them.
    """
    wants_restock = (
        reason_code in GOODS_RETURN_REASONS
        and original.sale_id is not None
        and any(line.get("sale_line") for line in lines)
    )

    if not wants_restock:
        return issue_credit_note(
            original,
            lines=[_financial_line(line) for line in lines],
            reason=reason,
            reason_code=reason_code,
            actor=actor,
        )

    from apps.sales.models import SaleLine
    from apps.sales.services import process_return

    # Only lines naming a sale line can be restocked; the rest would have no
    # batch to return into. Refusing the mixed case outright keeps the two
    # paths from silently splitting one correction across two documents.
    unlinked = [line for line in lines if not line.get("sale_line")]
    if unlinked:
        raise BusinessRuleViolation(
            _(
                "Every line of a goods-return credit note must reference the sale "
                "line it was billed from."
            ),
            code="sale_line_required",
        )

    sale_lines = {
        sale_line.pk: sale_line
        for sale_line in SaleLine.objects.filter(
            pk__in=[line["sale_line"] for line in lines], sale_id=original.sale_id
        ).select_related("product")
    }

    return_lines = []
    for line in lines:
        sale_line = sale_lines.get(line["sale_line"])
        if sale_line is None:
            # Either the id is unknown or it belongs to a different sale.
            # Both mean the caller is crediting stock this invoice never sold.
            raise BusinessRuleViolation(
                _("Sale line %(id)s does not belong to the invoice being credited.")
                % {"id": line["sale_line"]},
                code="sale_line_mismatch",
            )
        return_lines.append(
            {
                "sale_line": sale_line,
                "quantity": line["quantity"],
                "restock": bool(line.get("restock", False)),
                "condition_notes": line.get("condition_notes", ""),
            }
        )

    sale_return = process_return(
        original.sale,
        lines=return_lines,
        reason=reason,
        actor=actor,
        issue_credit_note=True,
    )

    if sale_return.credit_note is None:
        # process_return only raises a note when the sale has an invoice
        # attached. We reached here from that very invoice, so this means the
        # link was severed underneath us rather than anything the user did.
        raise BusinessRuleViolation(
            _("The return was recorded but no credit note could be raised against this invoice."),
            code="credit_note_not_raised",
        )

    note = sale_return.credit_note
    note.credit_reason_code = reason_code
    note.save(update_fields=["credit_reason_code"])
    return note


def _financial_line(line: dict) -> dict:
    """Strip the goods-return keys `create_invoice` does not accept."""
    return {
        key: value
        for key, value in line.items()
        if key not in {"sale_line", "restock", "condition_notes"}
    }


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


def register_print(invoice: Invoice, *, actor=None) -> int:
    """
    Claim the next copy number for a fiscal document, and return it.

    Returns the ordinal of *this* copy: 1 is the original, anything higher is
    a reprint. The printed page does not distinguish them, so the counter and
    its audit entries are the only record that a document was issued more than
    once — which is what a reprint query has to rely on.

    The number is claimed by an atomic increment and read back, rather than by
    reading the counter and then incrementing it, so two concurrent prints
    cannot both be recorded as copy #1.
    """
    invoice.print_count = F("print_count") + 1
    invoice.last_printed_at = timezone.now()
    invoice.save(update_fields=["print_count", "last_printed_at"])
    invoice.refresh_from_db(fields=["print_count"])
    copy_number = invoice.print_count

    record(
        AuditAction.PRINT,
        Invoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.invoice_number,
        notes=f"Document printed (copy #{copy_number})",
        actor=actor,
    )
    return copy_number


def release_print(invoice: Invoice, copy_number: int, *, reason: str, actor=None) -> None:
    """
    Give back a copy number claimed for a document that was never produced.

    `register_print` claims the number *before* rendering, so a render that
    fails outright (typically the PDF engine missing on the host) would
    otherwise leave the counter advanced, making the reprint history show
    copies that were never actually produced.

    The claim is released rather than the audit entry deleted: the log is a
    hash chain, so the PRINT entry stays and a compensating note is appended
    after it. The decrement is guarded to `print_count > 0` and applied as an
    atomic F() update so a concurrent print that legitimately claimed a later
    number is not clobbered.
    """
    updated = Invoice.objects.filter(pk=invoice.pk, print_count__gt=0).update(
        print_count=F("print_count") - 1, updated_at=timezone.now()
    )
    if updated:
        invoice.refresh_from_db(fields=["print_count"])

    record(
        AuditAction.PRINT,
        Invoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.invoice_number,
        notes=f"Print copy #{copy_number} released, not produced: {reason}",
        actor=actor,
    )
