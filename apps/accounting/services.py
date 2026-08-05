"""
Accounting orchestration and the reports built on it.

Every figure here is derived at read time from the documents that recorded the
underlying event — sales, supplier invoices, payments, expenses. Nothing is
posted to a running total, and there is no balance stored anywhere in this
module. That is a deliberate choice for a system with no ledger: a cached
total in an app that cannot close a period has nothing to reconcile against,
so an error in it would never be found.

The cost is that a report recomputes on each request. At the volume a single
wholesaler produces this is a handful of indexed aggregate queries, which is a
price worth paying for figures that cannot silently drift.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record, snapshot
from apps.core.exceptions import BusinessRuleViolation, InvalidStateTransition
from apps.core.models import AuditAction
from apps.core.money import q_internal
from apps.core.numbering import next_number

from .models import Expense, ExpenseCategory, ExpenseStatus

ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


@transaction.atomic
def create_expense(
    *,
    category,
    description: str,
    amount: Decimal,
    expense_date=None,
    paid_date=None,
    tax_amount: Decimal = ZERO,
    payment_method: str = "CASH",
    payment_reference: str = "",
    payee: str = "",
    supplier=None,
    purchase_order=None,
    receipt_number: str = "",
    notes: str = "",
    currency: str = "BIF",
    status: str | None = None,
    actor=None,
) -> Expense:
    """
    Record a business cost.

    Status follows what the caller tells us about payment: an expense entered
    with a `paid_date` is PAID, one without is RECORDED and outstanding. That
    inference is here rather than left to the caller so the cash-outflow report
    cannot be thrown off by a client that forgot to set a status.
    """
    amount = q_internal(amount)
    tax_amount = q_internal(tax_amount)
    expense_date = expense_date or timezone.localdate()

    if amount <= ZERO:
        raise BusinessRuleViolation(
            _("An expense amount must be greater than zero."), code="invalid_amount"
        )
    if tax_amount > amount:
        raise BusinessRuleViolation(
            _("The VAT on an expense cannot exceed the expense itself."),
            code="tax_exceeds_amount",
        )
    if not description or not description.strip():
        raise BusinessRuleViolation(
            _("A description is required so the expense can be identified later."),
            code="description_required",
        )

    if status is None:
        status = ExpenseStatus.PAID if paid_date else ExpenseStatus.RECORDED

    expense = Expense.objects.create(
        reference=next_number("accounting.expense", when=expense_date),
        category=category,
        status=status,
        description=description.strip(),
        notes=notes,
        expense_date=expense_date,
        paid_date=paid_date,
        amount=amount,
        tax_amount=tax_amount,
        currency=currency,
        payment_method=payment_method,
        payment_reference=payment_reference,
        payee=payee,
        supplier=supplier,
        purchase_order=purchase_order,
        receipt_number=receipt_number,
        recorded_by=actor,
        created_by=actor,
    )

    record(
        AuditAction.CREATE,
        Expense._meta.label,
        entity_id=str(expense.pk),
        entity_label=expense.reference,
        new_value=snapshot(expense),
        notes=f"Expense recorded: {expense.description} ({amount} {currency})",
        actor=actor,
    )
    return expense


@transaction.atomic
def update_expense(expense: Expense, *, actor=None, **fields) -> Expense:
    """
    Amend an expense that has not yet been settled or cancelled.

    Once PAID the record describes money that has moved, and once CANCELLED it
    is a decision on file; both are corrected by cancelling and re-entering
    rather than by editing, so the original entry remains visible.
    """
    if not expense.is_editable:
        raise InvalidStateTransition(
            _("An expense that is %(status)s can no longer be edited.")
            % {"status": expense.get_status_display()},
            details={"current_status": expense.status},
        )

    previous = snapshot(expense)

    editable = {
        "category", "description", "notes", "expense_date", "paid_date",
        "amount", "tax_amount", "payment_method", "payment_reference",
        "payee", "supplier", "purchase_order", "receipt_number", "currency",
    }
    for name, value in fields.items():
        if name in editable and value is not None:
            setattr(expense, name, value)

    expense.amount = q_internal(expense.amount)
    expense.tax_amount = q_internal(expense.tax_amount)

    if expense.amount <= ZERO:
        raise BusinessRuleViolation(
            _("An expense amount must be greater than zero."), code="invalid_amount"
        )
    if expense.tax_amount > expense.amount:
        raise BusinessRuleViolation(
            _("The VAT on an expense cannot exceed the expense itself."),
            code="tax_exceeds_amount",
        )

    # Supplying a payment date settles it, which is the same inference
    # `create_expense` makes — kept consistent so an expense marked paid by an
    # edit behaves identically to one entered as paid.
    if expense.paid_date and expense.status == ExpenseStatus.RECORDED:
        expense.status = ExpenseStatus.PAID

    expense.updated_by = actor
    expense.save()

    record(
        AuditAction.UPDATE,
        Expense._meta.label,
        entity_id=str(expense.pk),
        entity_label=expense.reference,
        previous_value=previous,
        new_value=snapshot(expense),
        notes=f"Expense amended: {expense.reference}",
        actor=actor,
    )
    return expense


@transaction.atomic
def approve_expense(expense: Expense, *, actor=None) -> Expense:
    """Approve an expense for payment."""
    if expense.status not in {ExpenseStatus.DRAFT, ExpenseStatus.RECORDED}:
        raise InvalidStateTransition(
            _("Only a recorded expense can be approved; this one is %(status)s.")
            % {"status": expense.get_status_display()},
            details={"current_status": expense.status},
        )

    expense.status = ExpenseStatus.APPROVED
    expense.approved_by = actor
    expense.approved_at = timezone.now()
    expense.updated_by = actor
    expense.save(
        update_fields=["status", "approved_by", "approved_at", "updated_by", "updated_at"]
    )

    record(
        AuditAction.APPROVE,
        Expense._meta.label,
        entity_id=str(expense.pk),
        entity_label=expense.reference,
        new_value={"status": expense.status, "amount": str(expense.amount)},
        changed_fields=["status"],
        notes=f"Expense approved: {expense.reference}",
        actor=actor,
    )
    return expense


@transaction.atomic
def mark_expense_paid(
    expense: Expense, *, paid_date=None, payment_method: str | None = None,
    payment_reference: str = "", actor=None,
) -> Expense:
    """Record that an expense has been settled."""
    if expense.status == ExpenseStatus.CANCELLED:
        raise InvalidStateTransition(
            _("A cancelled expense cannot be paid."),
            details={"current_status": expense.status},
        )
    if expense.is_paid:
        raise InvalidStateTransition(
            _("This expense is already marked as paid."),
            details={"current_status": expense.status},
        )

    expense.status = ExpenseStatus.PAID
    expense.paid_date = paid_date or timezone.localdate()
    if payment_method:
        expense.payment_method = payment_method
    if payment_reference:
        expense.payment_reference = payment_reference
    expense.updated_by = actor
    expense.save(
        update_fields=[
            "status", "paid_date", "payment_method", "payment_reference",
            "updated_by", "updated_at",
        ]
    )

    record(
        AuditAction.UPDATE,
        Expense._meta.label,
        entity_id=str(expense.pk),
        entity_label=expense.reference,
        new_value={
            "status": expense.status,
            "paid_date": str(expense.paid_date),
            "amount": str(expense.amount),
        },
        changed_fields=["status", "paid_date"],
        notes=f"Expense paid: {expense.reference}",
        actor=actor,
    )
    return expense


@transaction.atomic
def cancel_expense(expense: Expense, *, reason: str, actor=None) -> Expense:
    """
    Cancel an expense recorded in error.

    Soft state change rather than deletion, so a reference that was issued
    still resolves to something and the correction is visible next to the
    mistake.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to cancel an expense."), code="reason_required"
        )
    if expense.status == ExpenseStatus.CANCELLED:
        raise InvalidStateTransition(
            _("This expense has already been cancelled."),
            details={"current_status": expense.status},
        )

    previous_status = expense.status
    expense.status = ExpenseStatus.CANCELLED
    expense.cancelled_at = timezone.now()
    expense.cancellation_reason = reason
    expense.updated_by = actor
    expense.save(
        update_fields=[
            "status", "cancelled_at", "cancellation_reason", "updated_by", "updated_at",
        ]
    )

    record(
        AuditAction.CANCEL,
        Expense._meta.label,
        entity_id=str(expense.pk),
        entity_label=expense.reference,
        previous_value={"status": previous_status},
        new_value={"status": ExpenseStatus.CANCELLED},
        changed_fields=["status"],
        notes=f"Expense cancelled: {reason}",
        actor=actor,
    )
    return expense


@transaction.atomic
def delete_expense(expense: Expense, *, reason: str = "", actor=None) -> Expense:
    """
    Soft-delete an expense.

    Reserved for entries that should never have existed at all — a duplicate,
    or a test row. A genuine cost that turned out to be wrong is cancelled
    instead, which keeps it visible in the period it was recorded in.
    """
    if expense.is_paid:
        raise BusinessRuleViolation(
            _("A paid expense cannot be deleted. Cancel it instead so the payment stays on record."),
            code="expense_paid",
        )

    expense.delete(actor=actor)

    record(
        AuditAction.DELETE,
        Expense._meta.label,
        entity_id=str(expense.pk),
        entity_label=expense.reference,
        previous_value={"amount": str(expense.amount), "status": expense.status},
        notes=f"Expense deleted{f': {reason}' if reason else ''}",
        actor=actor,
    )
    return expense


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
#
# All four take a date window and share these conventions:
#
#   * Cancelled documents are excluded everywhere. A cancelled expense or
#     invoice is a decision on file, not a cost.
#   * Expense reports filter on `expense_date` (when the cost was incurred);
#     the cash-outflow report filters on `paid_date` and payment dates (when
#     money actually moved). Mixing the two is what makes a spend report and a
#     bank statement disagree.


def _category_label(row: dict, prefix: str = "category__") -> str:
    """
    The category name in the request's language, from a `.values()` row.

    Report aggregates go through `.values()`, which reads columns and so cannot
    use `ExpenseCategory.name` (a property). This resolves the same pair by the
    same rule, with French as the always-populated fallback, so a report and a
    list screen never disagree about what a category is called.
    """
    from django.utils.translation import get_language

    suffix = "fr" if (get_language() or "fr").startswith("fr") else "en"
    return row.get(f"{prefix}name_{suffix}") or row.get(f"{prefix}name_fr") or ""


def _expense_base(date_from=None, date_to=None, *, date_field: str = "expense_date"):
    """Non-cancelled expenses within a window, filtered on the given date."""
    queryset = Expense.objects.filter(deleted_at__isnull=True).exclude(
        status=ExpenseStatus.CANCELLED
    )
    if date_from:
        queryset = queryset.filter(**{f"{date_field}__gte": date_from})
    if date_to:
        queryset = queryset.filter(**{f"{date_field}__lte": date_to})
    return queryset


def expenses_by_category(*, date_from=None, date_to=None) -> dict:
    """
    Expense report grouped by category.

    Percentages are computed against the report's own total so the rows sum to
    100 within the window, which is what makes it readable as a breakdown.
    """
    queryset = _expense_base(date_from, date_to)

    rows = list(
        queryset.values(
            "category_id", "category__code", "category__name_fr",
            "category__name_en",
        )
        .annotate(
            expense_count=models.Count("id"),
            total_amount=models.Sum("amount"),
            total_tax=models.Sum("tax_amount"),
        )
        .order_by("-total_amount")
    )

    total = q_internal(sum((row["total_amount"] or ZERO) for row in rows))

    categories = []
    for row in rows:
        amount = q_internal(row["total_amount"] or ZERO)
        categories.append(
            {
                "category_id": str(row["category_id"]),
                "category_code": row["category__code"],
                # `.values()` cannot reach the model's `name` property, so the
                # language is resolved here from the same paired columns.
                "category_name": _category_label(row),
                "category_name_fr": row["category__name_fr"],
                "category_name_en": row["category__name_en"],
                "expense_count": row["expense_count"],
                "total_amount": amount,
                "total_tax": q_internal(row["total_tax"] or ZERO),
                "percentage": (
                    q_internal((amount / total) * Decimal("100")) if total > ZERO else ZERO
                ),
            }
        )

    return {
        "date_from": date_from,
        "date_to": date_to,
        "total_amount": total,
        "expense_count": sum(row["expense_count"] for row in rows),
        "category_count": len(categories),
        "categories": categories,
    }


def supplier_payment_report(*, date_from=None, date_to=None, supplier=None) -> dict:
    """
    Supplier payment report — what was paid out, to whom, and how.

    Reversed payments are excluded from the totals but the count of them is
    reported: a period with reversals is one where something went wrong, and
    hiding that entirely would be misleading.
    """
    from apps.purchasing.models import SupplierPayment

    queryset = SupplierPayment.objects.filter(deleted_at__isnull=True).select_related("supplier")
    if date_from:
        queryset = queryset.filter(payment_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(payment_date__lte=date_to)
    if supplier is not None:
        queryset = queryset.filter(supplier=supplier)

    active = queryset.filter(is_reversed=False)

    by_supplier = list(
        active.values("supplier_id", "supplier__supplier_code", "supplier__name")
        .annotate(
            payment_count=models.Count("id"),
            total_paid=models.Sum("amount"),
            total_allocated=models.Sum("allocated_amount"),
        )
        .order_by("-total_paid")
    )

    by_method = list(
        active.values("method")
        .annotate(payment_count=models.Count("id"), total_paid=models.Sum("amount"))
        .order_by("-total_paid")
    )

    total_paid = q_internal(
        active.aggregate(total=models.Sum("amount"))["total"] or ZERO
    )
    total_allocated = q_internal(
        active.aggregate(total=models.Sum("allocated_amount"))["total"] or ZERO
    )

    return {
        "date_from": date_from,
        "date_to": date_to,
        "total_paid": total_paid,
        "total_allocated": total_allocated,
        # Money paid that no invoice has claimed yet — sitting on account.
        "total_unallocated": q_internal(total_paid - total_allocated),
        "payment_count": active.count(),
        "reversed_count": queryset.filter(is_reversed=True).count(),
        "suppliers": [
            {
                "supplier_id": str(row["supplier_id"]),
                "supplier_code": row["supplier__supplier_code"],
                "supplier_name": row["supplier__name"],
                "payment_count": row["payment_count"],
                "total_paid": q_internal(row["total_paid"] or ZERO),
                "total_allocated": q_internal(row["total_allocated"] or ZERO),
            }
            for row in by_supplier
        ],
        "methods": [
            {
                "method": row["method"],
                "payment_count": row["payment_count"],
                "total_paid": q_internal(row["total_paid"] or ZERO),
            }
            for row in by_method
        ],
    }


def outstanding_supplier_balances(*, as_of=None) -> dict:
    """
    Outstanding supplier balances, with an ageing breakdown.

    The ageing buckets are the standard 30/60/90 bands, measured from the due
    date rather than the invoice date — what matters is how long a bill has
    been *late*, not how long ago it was issued.
    """
    from apps.purchasing.models import OPEN_SUPPLIER_INVOICE_STATUSES, SupplierInvoice

    as_of = as_of or timezone.localdate()

    invoices = (
        SupplierInvoice.objects.filter(
            status__in=OPEN_SUPPLIER_INVOICE_STATUSES,
            deleted_at__isnull=True,
            balance_due__gt=ZERO,
        )
        .select_related("supplier")
        .order_by("supplier__name", "due_date")
    )

    buckets = {"current": ZERO, "days_1_30": ZERO, "days_31_60": ZERO,
               "days_61_90": ZERO, "days_over_90": ZERO}
    per_supplier: dict = {}
    total = ZERO

    for invoice in invoices:
        balance = q_internal(invoice.balance_due)
        total = q_internal(total + balance)

        days_late = (as_of - invoice.due_date).days if invoice.due_date else 0
        if days_late <= 0:
            bucket = "current"
        elif days_late <= 30:
            bucket = "days_1_30"
        elif days_late <= 60:
            bucket = "days_31_60"
        elif days_late <= 90:
            bucket = "days_61_90"
        else:
            bucket = "days_over_90"
        buckets[bucket] = q_internal(buckets[bucket] + balance)

        entry = per_supplier.setdefault(
            invoice.supplier_id,
            {
                "supplier_id": str(invoice.supplier_id),
                "supplier_code": invoice.supplier.supplier_code,
                "supplier_name": invoice.supplier.name,
                "currency": invoice.currency,
                "invoice_count": 0,
                "outstanding_balance": ZERO,
                "overdue_amount": ZERO,
                "oldest_due_date": invoice.due_date,
                "invoices": [],
            },
        )
        entry["invoice_count"] += 1
        entry["outstanding_balance"] = q_internal(entry["outstanding_balance"] + balance)
        if days_late > 0:
            entry["overdue_amount"] = q_internal(entry["overdue_amount"] + balance)
        if invoice.due_date and (
            entry["oldest_due_date"] is None or invoice.due_date < entry["oldest_due_date"]
        ):
            entry["oldest_due_date"] = invoice.due_date
        entry["invoices"].append(
            {
                "id": str(invoice.pk),
                "reference": invoice.reference,
                "invoice_number": invoice.invoice_number,
                "invoice_date": invoice.invoice_date,
                "due_date": invoice.due_date,
                "total_amount": invoice.total_amount,
                "paid_amount": invoice.paid_amount,
                "balance_due": balance,
                "days_overdue": max(days_late, 0),
                "payment_progress": invoice.payment_progress,
            }
        )

    suppliers = sorted(
        per_supplier.values(), key=lambda row: row["outstanding_balance"], reverse=True
    )

    return {
        "as_of": as_of,
        "total_outstanding": total,
        "supplier_count": len(suppliers),
        "invoice_count": sum(row["invoice_count"] for row in suppliers),
        "ageing": buckets,
        "suppliers": suppliers,
    }


def cash_outflow(*, date_from, date_to) -> dict:
    """
    Everything that left the bank in a period, from both sources.

    Supplier payments and expenses are reported side by side and then summed,
    because "what did we spend" is one question and the answer lives in two
    tables. Both legs filter on when money *moved* — payment date and paid
    date — not when the obligation arose.

    Reversed supplier payments are excluded: the money came back, so including
    it would overstate the outflow for the period.
    """
    from apps.purchasing.models import SupplierPayment

    payments = SupplierPayment.objects.filter(
        deleted_at__isnull=True,
        is_reversed=False,
        payment_date__gte=date_from,
        payment_date__lte=date_to,
    )
    supplier_total = q_internal(
        payments.aggregate(total=models.Sum("amount"))["total"] or ZERO
    )

    # Only expenses actually settled in the window. An approved-but-unpaid
    # expense is a commitment, reported separately below.
    expenses = _expense_base(date_from, date_to, date_field="paid_date").filter(
        status=ExpenseStatus.PAID
    )
    expense_total = q_internal(
        expenses.aggregate(total=models.Sum("amount"))["total"] or ZERO
    )

    expense_rows = list(
        expenses.values(
            "category__code", "category__name_fr", "category__name_en",
        )
        .annotate(total=models.Sum("amount"), count=models.Count("id"))
        .order_by("-total")
    )

    # Costs incurred in the window but not yet settled — what is still to pay.
    unpaid = _expense_base(date_from, date_to).filter(
        status__in=[ExpenseStatus.DRAFT, ExpenseStatus.RECORDED, ExpenseStatus.APPROVED]
    )
    unpaid_total = q_internal(
        unpaid.aggregate(total=models.Sum("amount"))["total"] or ZERO
    )

    total = q_internal(supplier_total + expense_total)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "total_outflow": total,
        "supplier_payments_total": supplier_total,
        "supplier_payment_count": payments.count(),
        "expenses_total": expense_total,
        "expense_count": expenses.count(),
        "unpaid_expenses_total": unpaid_total,
        "expenses_by_category": [
            {
                "category_code": row["category__code"],
                "category_name": _category_label(row),
                "expense_count": row["count"],
                "total_amount": q_internal(row["total"] or ZERO),
            }
            for row in expense_rows
        ],
        "breakdown": [
            {"source": "SUPPLIER_PAYMENTS", "total_amount": supplier_total},
            {"source": "EXPENSES", "total_amount": expense_total},
        ],
    }


def financial_overview(*, date_from, date_to) -> dict:
    """
    The money-in / money-out picture for a period.

    Revenue and cost of goods come from confirmed sales, so the figure matches
    what the sales reports show rather than being derived a second way. Below
    that sit the two outflows this module knows about, and the result is an
    operating margin — not a statutory profit figure, and deliberately labelled
    as an indicator rather than a P&L.

    Cancelled and draft sales are excluded: a draft is a quotation and a
    cancelled sale never happened.
    """
    from apps.sales.models import Sale, SaleStatus

    revenue_statuses = [
        SaleStatus.CONFIRMED, SaleStatus.DELIVERED,
        SaleStatus.COMPLETED, SaleStatus.PARTIALLY_RETURNED,
    ]

    sales = Sale.objects.filter(
        deleted_at__isnull=True,
        status__in=revenue_statuses,
        sale_date__date__gte=date_from,
        sale_date__date__lte=date_to,
    )
    sales_totals = sales.aggregate(
        gross=models.Sum("total_amount"),
        tax=models.Sum("tax_amount"),
        cost=models.Sum("total_cost"),
        count=models.Count("id"),
    )

    gross_revenue = q_internal(sales_totals["gross"] or ZERO)
    sales_tax = q_internal(sales_totals["tax"] or ZERO)
    cost_of_goods = q_internal(sales_totals["cost"] or ZERO)
    # VAT collected is not revenue — it is held on behalf of the OBR.
    net_revenue = q_internal(gross_revenue - sales_tax)
    gross_profit = q_internal(net_revenue - cost_of_goods)

    outflow = cash_outflow(date_from=date_from, date_to=date_to)

    operating_expenses = q_internal(
        sum(row["total_amount"] for row in outflow["expenses_by_category"])
    )

    operating_result = q_internal(gross_profit - operating_expenses)

    from apps.purchasing.services import supplier_balances

    balances = supplier_balances()
    total_payable = q_internal(sum(row["outstanding_balance"] for row in balances))

    return {
        "date_from": date_from,
        "date_to": date_to,
        "currency": "BIF",
        # --- Money in -----------------------------------------------------
        "sales_count": sales_totals["count"] or 0,
        "gross_revenue": gross_revenue,
        "sales_tax": sales_tax,
        "net_revenue": net_revenue,
        "cost_of_goods": cost_of_goods,
        "gross_profit": gross_profit,
        "gross_margin_percent": (
            q_internal((gross_profit / net_revenue) * Decimal("100"))
            if net_revenue > ZERO
            else ZERO
        ),
        # --- Money out ----------------------------------------------------
        "operating_expenses": operating_expenses,
        "supplier_payments": outflow["supplier_payments_total"],
        "total_cash_outflow": outflow["total_outflow"],
        "unpaid_expenses": outflow["unpaid_expenses_total"],
        # --- Result -------------------------------------------------------
        "operating_result": operating_result,
        "operating_margin_percent": (
            q_internal((operating_result / net_revenue) * Decimal("100"))
            if net_revenue > ZERO
            else ZERO
        ),
        # --- Position -----------------------------------------------------
        "outstanding_payables": total_payable,
        "supplier_count_owed": len(balances),
    }


def seed_default_categories() -> tuple[int, int]:
    """
    Create the default expense categories. Idempotent.

    get_or_create rather than update_or_create, for the same reason
    `seed_sequences` uses it: re-running must never overwrite a category a
    business has renamed to suit itself.
    """
    from .models import DEFAULT_EXPENSE_CATEGORIES

    created = existing = 0
    for spec in DEFAULT_EXPENSE_CATEGORIES:
        _obj, was_created = ExpenseCategory.objects.get_or_create(
            code=spec["code"],
            defaults={
                "name_fr": spec["name_fr"],
                "name_en": spec.get("name_en", ""),
                "description_fr": spec.get("description_fr", ""),
                "description_en": spec.get("description_en", ""),
            },
        )
        if was_created:
            created += 1
        else:
            existing += 1
    return created, existing
