"""
Reporting and dashboard analytics.

Design note: reports read from the operational tables directly. At the stated
scale (100 concurrent users, a single wholesaler's transaction volume) that is
correct — a separate reporting store would add synchronisation complexity and
a staleness window for no benefit. The indexes in each app are chosen to serve
these queries; if volume outgrows them, the migration path is a materialised
view per report, not a rewrite of this module.

Every figure is derived from posted documents only. Including drafts would
report revenue that does not exist.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.utils import timezone

from apps.core.money import q_internal

ZERO = Decimal("0")
_MONEY = DecimalField(max_digits=18, decimal_places=4)


def _end_of_day(value):
    """
    Widen a bare date used as an inclusive upper bound to the end of that day.

    The report filters compare against DateTimeFields. A plain `date` is
    interpreted as midnight, so `sale_date__lte=2026-08-02` silently excludes
    everything that happened *during* the 2nd — which is every sale made today.
    Anything already carrying a time is left alone.
    """
    if isinstance(value, dt.datetime) or not isinstance(value, dt.date):
        return value

    end = dt.datetime.combine(value, dt.time.max)
    if timezone.is_naive(end) and timezone.get_current_timezone() is not None:
        end = timezone.make_aware(end)
    return end


def _money_sum(field: str):
    """Sum that yields 0 rather than None on an empty set."""
    return Coalesce(Sum(field), Value(ZERO), output_field=_MONEY)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def dashboard_kpis(*, warehouse=None) -> dict:
    """Headline figures for the executive dashboard."""
    from apps.catalog.models import Medicine, ProductStatus
    from apps.inventory.models import BatchStatus, StockBatch
    from apps.invoicing.models import Invoice, InvoiceStatus
    from apps.sales.models import Sale, SaleStatus

    today = timezone.localdate()
    month_start = today.replace(day=1)

    batches = StockBatch.objects.filter(
        status=BatchStatus.ACTIVE, quantity_remaining__gt=ZERO, deleted_at__isnull=True,
    )
    if warehouse:
        batches = batches.filter(warehouse=warehouse)

    inventory_value = ZERO
    for row in batches.values("quantity_remaining", "landed_unit_cost"):
        inventory_value += row["quantity_remaining"] * row["landed_unit_cost"]

    # Sellable stock per product: excludes expired and reserved units, so a
    # product sitting on expired stock correctly reads as out of stock.
    stock_levels = dict(
        batches.filter(expiry_date__gte=today)
        .values_list("product_id")
        .annotate(total=Sum(F("quantity_remaining") - F("quantity_reserved")))
    )

    active_products = Medicine.objects.filter(
        status=ProductStatus.ACTIVE, deleted_at__isnull=True,
    )
    low_stock = out_of_stock = 0
    for product in active_products.only("id", "reorder_level").iterator(chunk_size=500):
        on_hand = stock_levels.get(product.pk) or ZERO
        if on_hand <= ZERO:
            out_of_stock += 1
        elif product.reorder_level > ZERO and on_hand <= product.reorder_level:
            low_stock += 1

    expiring_90 = batches.filter(
        expiry_date__gte=today, expiry_date__lte=today + timezone.timedelta(days=90),
    ).count()
    expired = StockBatch.objects.filter(
        expiry_date__lt=today, quantity_remaining__gt=ZERO, deleted_at__isnull=True,
    ).exclude(status__in=[BatchStatus.DISPOSED]).count()

    confirmed_sales = Sale.objects.filter(
        deleted_at__isnull=True,
    ).exclude(status__in=[SaleStatus.DRAFT, SaleStatus.CANCELLED])

    daily = confirmed_sales.filter(sale_date__date=today).aggregate(
        total=_money_sum("total_amount"), cost=_money_sum("total_cost"), count=Count("id"),
    )
    monthly = confirmed_sales.filter(sale_date__date__gte=month_start).aggregate(
        total=_money_sum("total_amount"), cost=_money_sum("total_cost"), count=Count("id"),
    )

    open_invoices = Invoice.objects.filter(
        status__in=[InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE],
        deleted_at__isnull=True,
    )
    receivables = open_invoices.aggregate(
        total=_money_sum("balance_due"), count=Count("id"),
    )
    overdue = open_invoices.filter(due_date__lt=today).aggregate(
        total=_money_sum("balance_due"), count=Count("id"),
    )

    return {
        "inventory": {
            "total_value": q_internal(inventory_value),
            "total_products": active_products.count(),
            "total_batches": batches.count(),
            "low_stock_products": low_stock,
            "out_of_stock_products": out_of_stock,
            "expiring_90_days": expiring_90,
            "expired_batches": expired,
        },
        "sales": {
            "daily_revenue": q_internal(daily["total"]),
            "daily_transactions": daily["count"],
            "daily_margin": q_internal(daily["total"] - daily["cost"]),
            "monthly_revenue": q_internal(monthly["total"]),
            "monthly_transactions": monthly["count"],
            "monthly_margin": q_internal(monthly["total"] - monthly["cost"]),
        },
        "receivables": {
            "outstanding_total": q_internal(receivables["total"]),
            "outstanding_count": receivables["count"],
            "overdue_total": q_internal(overdue["total"]),
            "overdue_count": overdue["count"],
        },
        "currency": "BIF",
        "as_of": timezone.now(),
    }


def revenue_trend(*, days: int = 30) -> list[dict]:
    """Daily revenue and margin for the trend chart."""
    from apps.sales.models import Sale, SaleStatus

    start = timezone.localdate() - timezone.timedelta(days=days)
    rows = (
        Sale.objects.filter(sale_date__date__gte=start, deleted_at__isnull=True)
        .exclude(status__in=[SaleStatus.DRAFT, SaleStatus.CANCELLED])
        .annotate(day=TruncDate("sale_date"))
        .values("day")
        .annotate(
            revenue=_money_sum("total_amount"),
            cost=_money_sum("total_cost"),
            transactions=Count("id"),
        )
        .order_by("day")
    )
    return [
        {
            "date": row["day"],
            "revenue": q_internal(row["revenue"]),
            "cost": q_internal(row["cost"]),
            "margin": q_internal(row["revenue"] - row["cost"]),
            "transactions": row["transactions"],
        }
        for row in rows
    ]


def top_customers(*, limit: int = 10, days: int = 30) -> list[dict]:
    from apps.sales.models import Sale, SaleStatus

    start = timezone.localdate() - timezone.timedelta(days=days)
    rows = (
        Sale.objects.filter(sale_date__date__gte=start, deleted_at__isnull=True)
        .exclude(status__in=[SaleStatus.DRAFT, SaleStatus.CANCELLED])
        .values("customer__id", "customer__customer_code", "customer__business_name")
        .annotate(revenue=_money_sum("total_amount"), transactions=Count("id"))
        .order_by("-revenue")[:limit]
    )
    return [
        {
            "customer_id": row["customer__id"],
            "customer_code": row["customer__customer_code"],
            "business_name": row["customer__business_name"],
            "revenue": q_internal(row["revenue"]),
            "transactions": row["transactions"],
        }
        for row in rows
    ]


def top_products(*, limit: int = 10, days: int = 30) -> list[dict]:
    from apps.sales.models import SaleLine, SaleStatus

    start = timezone.localdate() - timezone.timedelta(days=days)
    rows = (
        SaleLine.objects.filter(sale__sale_date__date__gte=start, sale__deleted_at__isnull=True)
        .exclude(sale__status__in=[SaleStatus.DRAFT, SaleStatus.CANCELLED])
        .values("product__id", "product__product_code", "product__name_fr")
        .annotate(
            quantity=Coalesce(Sum("quantity"), Value(ZERO), output_field=_MONEY),
            revenue=_money_sum("line_total"),
            cost=_money_sum("line_cost"),
        )
        .order_by("-revenue")[:limit]
    )
    return [
        {
            "product_id": row["product__id"],
            "product_code": row["product__product_code"],
            "name": row["product__name_fr"],
            "quantity_sold": q_internal(row["quantity"]),
            "revenue": q_internal(row["revenue"]),
            "margin": q_internal(row["revenue"] - row["cost"]),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Inventory reports
# ---------------------------------------------------------------------------


def inventory_valuation_report(*, warehouse=None, category=None) -> dict:
    from apps.inventory.models import BatchStatus, StockBatch

    batches = (
        StockBatch.objects.filter(
            status__in=[BatchStatus.ACTIVE, BatchStatus.QUARANTINED],
            quantity_remaining__gt=ZERO,
            deleted_at__isnull=True,
        )
        .select_related("product", "product__category", "warehouse")
        .order_by("product__name_fr", "expiry_date")
    )
    if warehouse:
        batches = batches.filter(warehouse=warehouse)
    if category:
        batches = batches.filter(product__category=category)

    lines, total_value, total_units = [], ZERO, ZERO
    for batch in batches.iterator(chunk_size=500):
        value = q_internal(batch.quantity_remaining * batch.landed_unit_cost)
        total_value += value
        total_units += batch.quantity_remaining
        lines.append(
            {
                "product_code": batch.product.product_code,
                "product_name": batch.product.name,
                "category": batch.product.category.name,
                "warehouse": batch.warehouse.code,
                "batch_number": batch.batch_number,
                "expiry_date": batch.expiry_date,
                "days_to_expiry": batch.days_to_expiry,
                "quantity": batch.quantity_remaining,
                "unit_cost": batch.landed_unit_cost,
                "value": value,
            }
        )

    return {
        "lines": lines,
        "total_value": q_internal(total_value),
        "total_units": q_internal(total_units),
        "batch_count": len(lines),
        "generated_at": timezone.now(),
        "currency": "BIF",
    }


def expiry_report(*, days: int = 180, warehouse=None) -> dict:
    """Batches expiring within a horizon, with value at risk."""
    from apps.inventory.models import BatchStatus, StockBatch

    today = timezone.localdate()
    cutoff = today + timezone.timedelta(days=days)

    batches = (
        StockBatch.objects.filter(
            status__in=[BatchStatus.ACTIVE, BatchStatus.QUARANTINED],
            quantity_remaining__gt=ZERO,
            expiry_date__lte=cutoff,
            deleted_at__isnull=True,
        )
        .select_related("product", "warehouse", "supplier")
        .order_by("expiry_date")
    )
    if warehouse:
        batches = batches.filter(warehouse=warehouse)

    lines, value_at_risk, already_expired = [], ZERO, ZERO
    for batch in batches.iterator(chunk_size=500):
        value = q_internal(batch.quantity_remaining * batch.landed_unit_cost)
        value_at_risk += value
        if batch.expiry_date < today:
            already_expired += value
        lines.append(
            {
                "product_code": batch.product.product_code,
                "product_name": batch.product.name,
                "batch_number": batch.batch_number,
                "warehouse": batch.warehouse.code,
                "supplier": batch.supplier.name if batch.supplier else "",
                "expiry_date": batch.expiry_date,
                "days_to_expiry": batch.days_to_expiry,
                "quantity": batch.quantity_remaining,
                "unit_cost": batch.landed_unit_cost,
                "value_at_risk": value,
                "is_expired": batch.expiry_date < today,
            }
        )

    return {
        "lines": lines,
        "horizon_days": days,
        "total_value_at_risk": q_internal(value_at_risk),
        "already_expired_value": q_internal(already_expired),
        "batch_count": len(lines),
        "generated_at": timezone.now(),
        "currency": "BIF",
    }


def stock_movement_report(*, date_from=None, date_to=None, product=None, warehouse=None) -> dict:
    from apps.inventory.models import StockMovement

    movements = (
        StockMovement.objects.select_related("product", "batch", "warehouse", "performed_by")
        .order_by("-performed_at")
    )
    if date_from:
        movements = movements.filter(performed_at__gte=date_from)
    if date_to:
        movements = movements.filter(performed_at__lte=_end_of_day(date_to))
    if product:
        movements = movements.filter(product=product)
    if warehouse:
        movements = movements.filter(warehouse=warehouse)

    lines = [
        {
            "date": m.performed_at,
            "product_code": m.product.product_code,
            "product_name": m.product.name,
            "batch_number": m.batch.batch_number,
            "warehouse": m.warehouse.code,
            "movement_type": m.get_movement_type_display(),
            "quantity_delta": m.quantity_delta,
            "balance_after": m.balance_after,
            "unit_cost": m.unit_cost,
            "value": m.total_value,
            "reference": m.source_reference,
            "performed_by": m.performed_by.get_full_name() if m.performed_by else "",
            "reason": m.reason,
        }
        for m in movements.iterator(chunk_size=500)
    ]

    return {
        "lines": lines,
        "movement_count": len(lines),
        "generated_at": timezone.now(),
    }


def dead_stock_report(*, days_without_movement: int = 180, warehouse=None) -> dict:
    """
    Stock that has not moved in a long time.

    Capital tied up in unsellable inventory. Measured from the last *issue*,
    not the last movement of any kind — a receipt or adjustment does not mean
    the product is selling.
    """
    from apps.inventory.models import BatchStatus, MovementType, StockBatch, StockMovement

    cutoff = timezone.now() - timezone.timedelta(days=days_without_movement)

    recently_issued = set(
        StockMovement.objects.filter(
            movement_type=MovementType.ISSUE, performed_at__gte=cutoff,
        ).values_list("product_id", flat=True)
    )

    batches = StockBatch.objects.filter(
        status=BatchStatus.ACTIVE, quantity_remaining__gt=ZERO, deleted_at__isnull=True,
    ).exclude(product_id__in=recently_issued).select_related("product", "warehouse")
    if warehouse:
        batches = batches.filter(warehouse=warehouse)

    lines, total_value = [], ZERO
    for batch in batches.iterator(chunk_size=500):
        value = q_internal(batch.quantity_remaining * batch.landed_unit_cost)
        total_value += value
        lines.append(
            {
                "product_code": batch.product.product_code,
                "product_name": batch.product.name,
                "batch_number": batch.batch_number,
                "warehouse": batch.warehouse.code,
                "quantity": batch.quantity_remaining,
                "value": value,
                "expiry_date": batch.expiry_date,
                "received_at": batch.received_at,
            }
        )

    return {
        "lines": lines,
        "threshold_days": days_without_movement,
        "total_value": q_internal(total_value),
        "generated_at": timezone.now(),
        "currency": "BIF",
    }


# ---------------------------------------------------------------------------
# Sales & financial reports
# ---------------------------------------------------------------------------


def sales_report(*, date_from, date_to, group_by: str = "day", customer=None, user=None) -> dict:
    from apps.sales.models import Sale, SaleStatus

    sales = (
        Sale.objects.filter(
            sale_date__gte=date_from, sale_date__lte=_end_of_day(date_to),
            deleted_at__isnull=True,
        )
        .exclude(status__in=[SaleStatus.DRAFT, SaleStatus.CANCELLED])
    )
    if customer:
        sales = sales.filter(customer=customer)
    if user:
        sales = sales.filter(salesperson=user)

    truncate = TruncMonth("sale_date") if group_by == "month" else TruncDate("sale_date")
    rows = (
        sales.annotate(period=truncate)
        .values("period")
        .annotate(
            revenue=_money_sum("total_amount"),
            tax=_money_sum("tax_amount"),
            cost=_money_sum("total_cost"),
            discount=_money_sum("discount_amount"),
            transactions=Count("id"),
        )
        .order_by("period")
    )

    lines = []
    totals = {"revenue": ZERO, "tax": ZERO, "cost": ZERO, "discount": ZERO, "transactions": 0}
    for row in rows:
        net = q_internal(row["revenue"] - row["tax"])
        margin = q_internal(net - row["cost"])
        lines.append(
            {
                "period": row["period"],
                "revenue": q_internal(row["revenue"]),
                "tax": q_internal(row["tax"]),
                "net_revenue": net,
                "cost": q_internal(row["cost"]),
                "margin": margin,
                "margin_percent": q_internal(margin / net * 100) if net > ZERO else ZERO,
                "discount": q_internal(row["discount"]),
                "transactions": row["transactions"],
            }
        )
        totals["revenue"] += row["revenue"]
        totals["tax"] += row["tax"]
        totals["cost"] += row["cost"]
        totals["discount"] += row["discount"]
        totals["transactions"] += row["transactions"]

    net_total = q_internal(totals["revenue"] - totals["tax"])
    return {
        "lines": lines,
        "date_from": date_from,
        "date_to": date_to,
        "group_by": group_by,
        "totals": {
            "revenue": q_internal(totals["revenue"]),
            "tax": q_internal(totals["tax"]),
            "net_revenue": net_total,
            "cost": q_internal(totals["cost"]),
            "margin": q_internal(net_total - totals["cost"]),
            "discount": q_internal(totals["discount"]),
            "transactions": totals["transactions"],
        },
        "generated_at": timezone.now(),
        "currency": "BIF",
    }


def receivables_ageing(*, as_of=None) -> dict:
    """
    Ageing of outstanding receivables.

    Standard 30/60/90 buckets. The oldest bucket is what drives collection
    escalation and, ultimately, bad-debt provisioning.
    """
    from apps.invoicing.models import Invoice, InvoiceStatus

    as_of = as_of or timezone.localdate()

    invoices = (
        Invoice.objects.filter(
            status__in=[InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE],
            balance_due__gt=ZERO,
            deleted_at__isnull=True,
        )
        .select_related("customer")
        .order_by("customer__business_name", "due_date")
    )

    buckets = {"current": ZERO, "1_30": ZERO, "31_60": ZERO, "61_90": ZERO, "over_90": ZERO}
    by_customer: dict = {}

    for invoice in invoices.iterator(chunk_size=500):
        days = (as_of - invoice.due_date).days if invoice.due_date else 0
        if days <= 0:
            bucket = "current"
        elif days <= 30:
            bucket = "1_30"
        elif days <= 60:
            bucket = "31_60"
        elif days <= 90:
            bucket = "61_90"
        else:
            bucket = "over_90"

        buckets[bucket] += invoice.balance_due

        entry = by_customer.setdefault(
            invoice.customer_id,
            {
                "customer_code": invoice.customer.customer_code,
                "business_name": invoice.customer.business_name,
                "credit_limit": invoice.customer.credit_limit,
                "current": ZERO, "1_30": ZERO, "31_60": ZERO, "61_90": ZERO,
                "over_90": ZERO, "total": ZERO,
            },
        )
        entry[bucket] += invoice.balance_due
        entry["total"] += invoice.balance_due

    return {
        "as_of": as_of,
        "buckets": {k: q_internal(v) for k, v in buckets.items()},
        "total": q_internal(sum(buckets.values())),
        "customers": sorted(
            by_customer.values(), key=lambda c: c["total"], reverse=True,
        ),
        "generated_at": timezone.now(),
        "currency": "BIF",
    }


def profit_and_loss(*, date_from, date_to) -> dict:
    """
    Gross trading result for a period.

    Gross margin only — operating expenses are not tracked by this system, so
    calling the output a full P&L would overstate what it represents. Labelled
    explicitly so nobody mistakes it for a statutory account.
    """
    from apps.sales.models import Sale, SaleStatus

    sales = (
        Sale.objects.filter(
            sale_date__gte=date_from, sale_date__lte=_end_of_day(date_to),
            deleted_at__isnull=True,
        )
        .exclude(status__in=[SaleStatus.DRAFT, SaleStatus.CANCELLED])
        .aggregate(
            revenue=_money_sum("total_amount"),
            tax=_money_sum("tax_amount"),
            cost=_money_sum("total_cost"),
            discount=_money_sum("discount_amount"),
        )
    )

    net_revenue = q_internal(sales["revenue"] - sales["tax"])
    cogs = q_internal(sales["cost"])
    gross_profit = q_internal(net_revenue - cogs)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "gross_revenue": q_internal(sales["revenue"]),
        "vat_collected": q_internal(sales["tax"]),
        "net_revenue": net_revenue,
        "cost_of_goods_sold": cogs,
        "gross_profit": gross_profit,
        "gross_margin_percent": (
            q_internal(gross_profit / net_revenue * 100) if net_revenue > ZERO else ZERO
        ),
        "discounts_granted": q_internal(sales["discount"]),
        "note": "Gross trading result. Operating expenses are not tracked by this system.",
        "generated_at": timezone.now(),
        "currency": "BIF",
    }
