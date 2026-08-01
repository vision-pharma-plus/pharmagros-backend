"""
Scheduled inventory surveillance.

Expiry management is the highest-value automation in a pharmaceutical
wholesaler. Stock silently becomes unsellable and legally undispensable, and
the loss is only noticed at stocktake — by which point nothing can be done.
These tasks surface the problem while there is still time to sell through,
return to supplier, or negotiate.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from celery import shared_task
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record
from apps.core.models import AuditAction

from .models import BatchStatus, StockBatch

logger = logging.getLogger(__name__)

ZERO = Decimal("0")

# Alert horizons in days, widest first. Multiple tiers because the appropriate
# response differs: at 180 days you re-price to move stock, at 30 you accept
# the write-off and plan destruction.
EXPIRY_HORIZONS = [180, 90, 30]

# Half-open bands (lower, upper] in days from today, so each batch triggers
# exactly one tier per scan rather than one alert per horizon it falls under.
# Verified non-overlapping and gap-free between 1 and 180 days.
EXPIRY_BANDS = [
    (upper, EXPIRY_HORIZONS[index + 1] if index + 1 < len(EXPIRY_HORIZONS) else 0)
    for index, upper in enumerate(EXPIRY_HORIZONS)
]


@shared_task(name="apps.inventory.tasks.scan_expiry_horizons")
def scan_expiry_horizons() -> dict:
    """Alert on batches approaching expiry, at several horizons."""
    from apps.notifications.models import NotificationCode, NotificationSeverity
    from apps.notifications.services import notify_permission_holders

    today = timezone.localdate()
    results = {}

    for horizon, lower_days in EXPIRY_BANDS:
        cutoff = today + timezone.timedelta(days=horizon)
        lower = today + timezone.timedelta(days=lower_days)
        # Every batch in this band expires more than `lower_days` out, so a
        # product only wants to hear about it here if its configured horizon
        # reaches at least that far.
        band_start_days = lower_days + 1

        batches = (
            StockBatch.objects.filter(
                status=BatchStatus.ACTIVE,
                quantity_remaining__gt=ZERO,
                expiry_date__gt=lower,
                expiry_date__lte=cutoff,
                deleted_at__isnull=True,
            )
            # Respect the product's own alert horizon: a batch is only reported
            # once it falls inside the window its product asks for. A fast mover
            # set to 30 days stays quiet in the 180- and 90-day tiers and first
            # appears at 30, instead of alerting six months out for stock that
            # routinely clears in a fortnight.
            #
            # Compared as integer days rather than by shifting expiry_date by an
            # F() interval: date ± F() arithmetic is not portable between the
            # SQLite used in tests and the PostgreSQL used in production.
            .filter(product__expiry_alert_days__gte=band_start_days)
            .select_related("product", "warehouse")
            .order_by("expiry_date")
        )

        count = batches.count()
        results[f"{horizon}d"] = count
        if not count:
            continue

        value = sum(
            (b.quantity_remaining * b.landed_unit_cost for b in batches), ZERO
        )
        severity = (
            NotificationSeverity.CRITICAL
            if horizon <= 30
            else NotificationSeverity.WARNING
            if horizon <= 90
            else NotificationSeverity.INFO
        )

        notify_permission_holders(
            permission_code="inventory.view_stock",
            code=NotificationCode.EXPIRING_SOON,
            title=str(
                _("%(count)d batches expire within %(days)d days")
                % {"count": count, "days": horizon}
            ),
            body=str(
                _("Stock value at risk: %(value)s BIF. Review the expiry report to prioritise clearance.")
                % {"value": value.quantize(Decimal('1'))}
            ),
            severity=severity,
            link=f"/inventory/reports/expiry?horizon={horizon}",
            dedupe_key=f"expiry-horizon-{horizon}-{today:%Y-%m}",
        )

    logger.info("expiry_scan_complete", extra={"results": results})
    return results


@shared_task(name="apps.inventory.tasks.quarantine_expired_batches")
def quarantine_expired_batches() -> dict:
    """
    Move batches that crossed expiry overnight out of ACTIVE.

    Belt and braces: `allocate_fifo` already filters on `expiry_date >= today`,
    so an expired batch cannot be sold even if this task never ran. Flipping
    the status makes the condition visible in listings and reports, and means
    the exclusion does not rest on a single query filter.
    """
    today = timezone.localdate()

    expired = StockBatch.objects.filter(
        status=BatchStatus.ACTIVE,
        expiry_date__lt=today,
        deleted_at__isnull=True,
    ).select_related("product")

    batches = list(expired)
    count = len(batches)
    total_value = ZERO

    for batch in batches:
        total_value += batch.quantity_remaining * batch.landed_unit_cost

    if count:
        expired.update(status=BatchStatus.EXPIRED, updated_at=timezone.now())

        record(
            AuditAction.STOCK_MOVEMENT,
            "inventory.StockBatch",
            entity_label=f"{count} batches",
            new_value={
                "status": BatchStatus.EXPIRED,
                "batch_count": count,
                "value_at_cost": str(total_value),
            },
            notes=f"Automatic quarantine of {count} expired batches",
        )

        from apps.notifications.models import NotificationCode, NotificationSeverity
        from apps.notifications.services import notify_permission_holders

        notify_permission_holders(
            permission_code="inventory.dispose_stock",
            code=NotificationCode.EXPIRED,
            title=str(_("%(count)d batches have expired") % {"count": count}),
            body=str(
                _("Value at cost: %(value)s BIF. These batches are quarantined and must be scheduled for destruction.")
                % {"value": total_value.quantize(Decimal('1'))}
            ),
            severity=NotificationSeverity.CRITICAL,
            link="/inventory/batches?status=EXPIRED",
            dedupe_key=f"expired-batches-{today:%Y-%m-%d}",
        )

    logger.info("expiry_quarantine_complete", extra={"count": count})
    return {"quarantined": count, "value_at_cost": str(total_value)}


@shared_task(name="apps.inventory.tasks.scan_reorder_levels")
def scan_reorder_levels() -> dict:
    """Alert on products at or below their reorder level."""
    from apps.catalog.models import Medicine, ProductStatus
    from apps.notifications.models import NotificationCode, NotificationSeverity
    from apps.notifications.services import notify_permission_holders

    today = timezone.localdate()

    # Aggregate sellable stock per product: ACTIVE, unexpired batches only.
    # Counting expired or quarantined stock would mask a genuine shortage.
    stock_by_product = dict(
        StockBatch.objects.filter(
            status=BatchStatus.ACTIVE,
            expiry_date__gte=today,
            deleted_at__isnull=True,
        )
        .values_list("product_id")
        .annotate(total=Sum(F("quantity_remaining") - F("quantity_reserved")))
    )

    low, out = [], []
    products = Medicine.objects.filter(
        status=ProductStatus.ACTIVE, deleted_at__isnull=True,
    ).exclude(reorder_level=ZERO)

    for product in products.iterator(chunk_size=500):
        on_hand = stock_by_product.get(product.pk, ZERO) or ZERO
        if on_hand <= ZERO:
            out.append((product, on_hand))
        elif on_hand <= product.reorder_level:
            low.append((product, on_hand))

    if out:
        notify_permission_holders(
            permission_code="purchasing.add_order",
            code=NotificationCode.OUT_OF_STOCK,
            title=str(_("%(count)d products are out of stock") % {"count": len(out)}),
            body=str(
                _("Affected products: %(names)s")
                % {"names": ", ".join(p.name for p, _q in out[:10])}
            ),
            severity=NotificationSeverity.CRITICAL,
            link="/inventory/stock?filter=out_of_stock",
            dedupe_key=f"out-of-stock-{today:%Y-%m-%d}",
        )

    if low:
        notify_permission_holders(
            permission_code="purchasing.add_order",
            code=NotificationCode.LOW_STOCK,
            title=str(_("%(count)d products are below their reorder level") % {"count": len(low)}),
            body=str(
                _("Affected products: %(names)s")
                % {"names": ", ".join(p.name for p, _q in low[:10])}
            ),
            severity=NotificationSeverity.WARNING,
            link="/inventory/stock?filter=low_stock",
            dedupe_key=f"low-stock-{today:%Y-%m-%d}",
        )

    logger.info("reorder_scan_complete", extra={"low": len(low), "out": len(out)})
    return {"low_stock": len(low), "out_of_stock": len(out)}


@shared_task(name="apps.inventory.tasks.release_expired_reservations")
def release_expired_reservations() -> int:
    """
    Release stock held by abandoned drafts.

    Without this, a draft sale that is never confirmed locks stock
    indefinitely and the warehouse appears short of goods it actually holds.
    """
    from .models import StockReservation
    from .services import release_reservations

    stale = (
        StockReservation.objects.filter(
            released_at__isnull=True, expires_at__lt=timezone.now(),
        )
        .values_list("source_type", "source_id")
        .distinct()
    )

    released = 0
    for source_type, source_id in stale:
        released += release_reservations(source_type=source_type, source_id=source_id)

    if released:
        logger.info("stale_reservations_released", extra={"count": released})
    return released


@shared_task(name="apps.inventory.tasks.detect_stock_discrepancies")
def detect_stock_discrepancies() -> dict:
    """
    Compare cached batch balances against the ledger.

    Should always find nothing. A discrepancy means either a bug in the write
    path or direct database manipulation, both of which are incidents.
    """
    from apps.notifications.models import NotificationCode, NotificationSeverity
    from apps.notifications.services import notify_administrators

    from .services import find_discrepancies

    discrepancies = find_discrepancies()

    if discrepancies:
        logger.error(
            "stock_discrepancies_detected", extra={"count": len(discrepancies)},
        )
        notify_administrators(
            code=NotificationCode.STOCK_DISCREPANCY,
            title=str(
                _("%(count)d stock discrepancies detected") % {"count": len(discrepancies)}
            ),
            body=str(
                _("Batch balances disagree with the stock ledger. This indicates a data integrity failure and requires investigation.")
            ),
            severity=NotificationSeverity.CRITICAL,
            link="/inventory/reports/reconciliation",
        )

    return {"discrepancies": len(discrepancies), "details": discrepancies[:20]}
