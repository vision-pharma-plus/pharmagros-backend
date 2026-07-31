"""Customer and supplier services, including credit control."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record, snapshot
from apps.core.exceptions import BusinessRuleViolation, CreditLimitExceeded
from apps.core.models import AuditAction
from apps.core.money import q_internal
from apps.core.numbering import next_number

from .models import CreditLimitChange, Customer, PartnerStatus, PaymentTerms, Supplier

ZERO = Decimal("0")


@transaction.atomic
def create_customer(*, actor=None, **data) -> Customer:
    if not data.get("customer_code"):
        data["customer_code"] = next_number("partners.customer")

    # Institutional customers on credit terms must be identifiable to the tax
    # authority; a credit invoice without a NIF is not compliant.
    terms = data.get("payment_terms", PaymentTerms.CASH)
    if terms != PaymentTerms.CASH and not data.get("nif"):
        raise BusinessRuleViolation(
            _("A NIF is required for any customer with credit payment terms."),
            code="nif_required_for_credit",
        )

    customer = Customer.objects.create(created_by=actor, **data)

    if customer.credit_limit > ZERO:
        CreditLimitChange.objects.create(
            customer=customer, old_limit=ZERO, new_limit=customer.credit_limit,
            reason=str(_("Initial credit limit set on account creation")), changed_by=actor,
        )

    record(
        AuditAction.CREATE,
        Customer._meta.label,
        entity_id=str(customer.pk),
        entity_label=str(customer),
        new_value=snapshot(customer),
        actor=actor,
    )
    return customer


@transaction.atomic
def update_customer(customer: Customer, *, actor=None, **data) -> Customer:
    """Update a customer; credit limit changes are routed to set_credit_limit."""
    before = snapshot(customer)

    new_limit = data.pop("credit_limit", None)
    limit_reason = data.pop("credit_limit_reason", "")

    for field, value in data.items():
        setattr(customer, field, value)
    customer.updated_by = actor
    customer.save()

    if new_limit is not None and new_limit != customer.credit_limit:
        set_credit_limit(customer, new_limit, reason=limit_reason, actor=actor)

    record(
        AuditAction.UPDATE,
        Customer._meta.label,
        entity_id=str(customer.pk),
        entity_label=str(customer),
        previous_value=before,
        new_value=snapshot(customer),
        actor=actor,
    )
    return customer


@transaction.atomic
def set_credit_limit(
    customer: Customer, new_limit: Decimal, *, reason: str = "", actor=None
) -> CreditLimitChange:
    """
    Change a credit limit, journaling who authorised the exposure.

    Lowering a limit below the current outstanding balance is permitted — it
    is the correct response to a deteriorating payer — but it is recorded
    prominently, because it immediately blocks further credit sales and the
    sales floor will need to know why.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required for any credit limit change."), code="reason_required"
        )
    if new_limit < ZERO:
        raise BusinessRuleViolation(
            _("A credit limit cannot be negative."), code="invalid_credit_limit"
        )

    old_limit = customer.credit_limit
    if old_limit == new_limit:
        raise BusinessRuleViolation(
            _("The submitted credit limit is identical to the current one."),
            code="no_change",
        )

    below_balance = new_limit < customer.outstanding_balance

    customer.credit_limit = new_limit
    customer.updated_by = actor
    customer.save(update_fields=["credit_limit", "updated_by", "updated_at"])

    change = CreditLimitChange.objects.create(
        customer=customer, old_limit=old_limit, new_limit=new_limit,
        reason=reason, changed_by=actor,
    )

    record(
        AuditAction.UPDATE,
        Customer._meta.label,
        entity_id=str(customer.pk),
        entity_label=str(customer),
        previous_value={"credit_limit": str(old_limit)},
        new_value={"credit_limit": str(new_limit)},
        changed_fields=["credit_limit"],
        notes=(
            f"Credit limit changed: {reason}"
            + (
                " [WARNING: new limit is below the current outstanding balance]"
                if below_balance
                else ""
            )
        ),
        actor=actor,
    )
    return change


@transaction.atomic
def block_credit(customer: Customer, *, reason: str, actor=None) -> Customer:
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to block credit."), code="reason_required"
        )
    customer.credit_blocked = True
    customer.credit_block_reason = reason
    customer.updated_by = actor
    customer.save(
        update_fields=["credit_blocked", "credit_block_reason", "updated_by", "updated_at"]
    )
    record(
        AuditAction.UPDATE,
        Customer._meta.label,
        entity_id=str(customer.pk),
        entity_label=str(customer),
        new_value={"credit_blocked": True},
        changed_fields=["credit_blocked"],
        notes=f"Credit blocked: {reason}",
        actor=actor,
    )
    return customer


@transaction.atomic
def unblock_credit(customer: Customer, *, reason: str = "", actor=None) -> Customer:
    customer.credit_blocked = False
    customer.credit_block_reason = ""
    customer.updated_by = actor
    customer.save(
        update_fields=["credit_blocked", "credit_block_reason", "updated_by", "updated_at"]
    )
    record(
        AuditAction.UPDATE,
        Customer._meta.label,
        entity_id=str(customer.pk),
        entity_label=str(customer),
        new_value={"credit_blocked": False},
        changed_fields=["credit_blocked"],
        notes=f"Credit unblocked: {reason}" if reason else "Credit unblocked",
        actor=actor,
    )
    return customer


def check_credit(customer: Customer, amount: Decimal, *, raise_on_fail: bool = True) -> bool:
    """
    Gate a credit sale.

    Kept separate from `Customer.can_buy_on_credit` so the model stays free of
    exception-raising policy and the service layer owns the decision to block.
    """
    allowed, reason = customer.can_buy_on_credit(q_internal(amount))
    if not allowed and raise_on_fail:
        raise CreditLimitExceeded(
            reason,
            details={
                "customer_id": str(customer.pk),
                "customer_code": customer.customer_code,
                "credit_limit": str(customer.credit_limit),
                "outstanding_balance": str(customer.outstanding_balance),
                "available_credit": str(customer.available_credit),
                "requested_amount": str(amount),
            },
        )
    return allowed


@transaction.atomic
def recompute_balance(customer: Customer) -> Decimal:
    """
    Recalculate the outstanding balance from posted invoices.

    Derived rather than incremented. A running tally accumulates error from
    every cancelled invoice, reversed payment or partially applied credit
    note, and a wrong balance silently disables credit control — the failure
    is invisible until a customer is over-extended.
    """
    from apps.invoicing.models import Invoice, InvoiceStatus

    outstanding = (
        Invoice.objects.filter(
            customer=customer,
            status__in=[InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE],
            deleted_at__isnull=True,
        ).aggregate(total=Sum("balance_due"))["total"]
        or ZERO
    )

    outstanding = q_internal(outstanding)
    if customer.outstanding_balance != outstanding:
        previous = customer.outstanding_balance
        customer.outstanding_balance = outstanding
        customer.save(update_fields=["outstanding_balance", "updated_at"])
        record(
            AuditAction.UPDATE,
            Customer._meta.label,
            entity_id=str(customer.pk),
            entity_label=str(customer),
            previous_value={"outstanding_balance": str(previous)},
            new_value={"outstanding_balance": str(outstanding)},
            changed_fields=["outstanding_balance"],
            notes="Outstanding balance recomputed from posted invoices",
        )
    return outstanding


def customer_statement(customer: Customer, *, date_from=None, date_to=None) -> dict:
    """
    Account statement: invoices, payments and a running balance.

    Ordered chronologically so the running balance reads as an account
    ledger — which is what a customer disputing an amount will reconcile
    against their own records.
    """
    from apps.invoicing.models import Invoice, InvoiceStatus, Payment

    date_to = date_to or timezone.now()

    invoices = Invoice.objects.filter(
        customer=customer, deleted_at__isnull=True,
    ).exclude(status__in=[InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED])
    payments = Payment.objects.filter(customer=customer, deleted_at__isnull=True)

    if date_from:
        invoices = invoices.filter(invoice_date__gte=date_from)
        payments = payments.filter(payment_date__gte=date_from)
    invoices = invoices.filter(invoice_date__lte=date_to)
    payments = payments.filter(payment_date__lte=date_to)

    lines = []
    for invoice in invoices:
        lines.append(
            {
                "date": invoice.invoice_date,
                "type": "INVOICE",
                "reference": invoice.invoice_number,
                "debit": invoice.total_amount,
                "credit": ZERO,
                "due_date": invoice.due_date,
            }
        )
    for payment in payments:
        lines.append(
            {
                "date": payment.payment_date,
                "type": "PAYMENT",
                "reference": payment.reference,
                "debit": ZERO,
                "credit": payment.amount,
                "due_date": None,
            }
        )

    lines.sort(key=lambda line: (line["date"], line["type"]))

    running = ZERO
    for line in lines:
        running = q_internal(running + line["debit"] - line["credit"])
        line["balance"] = running

    return {
        "customer": customer,
        "date_from": date_from,
        "date_to": date_to,
        "lines": lines,
        "total_invoiced": q_internal(sum(line["debit"] for line in lines)),
        "total_paid": q_internal(sum(line["credit"] for line in lines)),
        "closing_balance": running,
        "currency": "BIF",
    }


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------


@transaction.atomic
def create_supplier(*, actor=None, **data) -> Supplier:
    if not data.get("supplier_code"):
        data["supplier_code"] = next_number("partners.supplier")

    supplier = Supplier.objects.create(created_by=actor, **data)
    record(
        AuditAction.CREATE,
        Supplier._meta.label,
        entity_id=str(supplier.pk),
        entity_label=str(supplier),
        new_value=snapshot(supplier),
        actor=actor,
    )
    return supplier


@transaction.atomic
def approve_supplier(supplier: Supplier, *, notes: str = "", actor=None) -> Supplier:
    """
    Approve a supplier for procurement.

    Pharmaceutical sourcing must be from qualified suppliers — buying from an
    unvetted source is both a quality risk and a regulatory finding. Purchase
    orders check `is_approved` before allowing supplier selection.
    """
    supplier.is_approved = True
    supplier.approval_notes = notes
    supplier.status = PartnerStatus.ACTIVE
    supplier.updated_by = actor
    supplier.save(
        update_fields=["is_approved", "approval_notes", "status", "updated_by", "updated_at"]
    )
    record(
        AuditAction.APPROVE,
        Supplier._meta.label,
        entity_id=str(supplier.pk),
        entity_label=str(supplier),
        new_value={"is_approved": True},
        changed_fields=["is_approved"],
        notes=f"Supplier approved: {notes}" if notes else "Supplier approved",
        actor=actor,
    )
    return supplier


def supplier_performance(supplier: Supplier, *, date_from=None, date_to=None) -> dict:
    """
    Delivery and quality metrics.

    On-time delivery is measured against the *expected* date on the order,
    not the lead time on the supplier record — the latter is a planning
    assumption, and scoring against it would flatter suppliers whose
    assumption is generous.
    """
    from apps.purchasing.models import PurchaseOrder, PurchaseOrderStatus

    orders = PurchaseOrder.objects.filter(supplier=supplier, deleted_at__isnull=True)
    if date_from:
        orders = orders.filter(order_date__gte=date_from)
    if date_to:
        orders = orders.filter(order_date__lte=date_to)

    total = orders.count()
    received = orders.filter(
        status__in=[PurchaseOrderStatus.RECEIVED, PurchaseOrderStatus.PARTIALLY_RECEIVED]
    )

    on_time = late = 0
    delay_days = 0
    for order in received:
        if order.actual_delivery_date and order.expected_delivery_date:
            if order.actual_delivery_date <= order.expected_delivery_date:
                on_time += 1
            else:
                late += 1
                delay_days += (order.actual_delivery_date - order.expected_delivery_date).days

    received_count = on_time + late
    return {
        "supplier": supplier,
        "total_orders": total,
        "received_orders": received_count,
        "on_time_deliveries": on_time,
        "late_deliveries": late,
        "on_time_rate": (
            q_internal(Decimal(on_time) / Decimal(received_count) * 100)
            if received_count
            else ZERO
        ),
        "average_delay_days": (
            q_internal(Decimal(delay_days) / Decimal(late)) if late else ZERO
        ),
        "total_purchase_value": q_internal(
            orders.aggregate(total=Sum("total_amount"))["total"] or ZERO
        ),
        "currency": "BIF",
    }
