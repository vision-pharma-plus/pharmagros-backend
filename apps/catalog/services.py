"""Catalogue services."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record, snapshot
from apps.core.exceptions import BusinessRuleViolation
from apps.core.models import AuditAction
from apps.core.numbering import next_number

from .models import Medicine, PriceHistory, ProductStatus


def _seed_opening_batch(
    medicine,
    *,
    batch_number: str,
    expiry_date,
    quantity,
    supplier_id=None,
    warehouse_id=None,
    actor=None,
    source_reference: str = "",
) -> None:
    """
    Open the batch the operator typed on the product form.

    The batch number is transcribed from the manufacturer's carton, so it is
    taken verbatim and never generated — a batch that does not match the
    printed lot code is useless in a recall.

    Quantity is whatever the operator counted. Zero is a legitimate answer:
    it registers the lot on the product without asserting stock that has not
    physically arrived, leaving the goods receipt to book the units.
    """
    from apps.inventory.models import StockBatch, Warehouse
    from apps.inventory.services import receive_stock

    # The serializer resolves these to model instances, while internal callers
    # pass a bare pk. Normalise so both work — filtering on pk with an
    # instance stringifies it and fails the UUID parse.
    def _pk(value):
        return getattr(value, "pk", value)

    warehouse = None
    if warehouse_id is not None:
        warehouse = Warehouse.objects.filter(
            pk=_pk(warehouse_id), is_active=True
        ).first()
    if warehouse is None:
        warehouse = (
            Warehouse.objects.filter(is_default=True, is_active=True).first()
            or Warehouse.objects.filter(is_active=True).first()
        )
    if warehouse is None:
        raise BusinessRuleViolation(
            _("No active warehouse is configured to receive this batch."),
            code="no_warehouse",
        )

    supplier = None
    if supplier_id is not None:
        from apps.partners.models import Supplier

        supplier = Supplier.objects.filter(pk=_pk(supplier_id)).first()

    quantity = Decimal(quantity or 0)

    existing = StockBatch.objects.filter(
        product=medicine,
        warehouse=warehouse,
        batch_number=batch_number,
        deleted_at__isnull=True,
    ).first()

    if existing is not None:
        # Same lot code, different expiry: either a transcription error or a
        # manufacturer reusing a number across runs. Both need a human
        # decision. Checked before the quantity branch so the zero path
        # refuses it too -- silently keeping the stored expiry would discard
        # what the operator read off the carton with no sign the two
        # disagreed.
        if existing.expiry_date != expiry_date:
            raise BusinessRuleViolation(
                _("Batch %(batch)s already exists with expiry %(existing)s, which differs from the %(new)s supplied.")
                % {
                    "batch": batch_number,
                    "existing": existing.expiry_date,
                    "new": expiry_date,
                },
                code="batch_expiry_conflict",
            )
        # A lot that already exists with the matching expiry is a repeat
        # submission, not a new delivery. Topping it up here would double-book
        # opening stock every time the product form was re-saved; genuine
        # additional units are booked on the receiving screen.
        return

    if quantity > 0:
        # Routed through receive_stock so the opening stock lands in the
        # ledger under the same expiry and costing rules as a delivery.
        receive_stock(
            product=medicine,
            warehouse=warehouse,
            batch_number=batch_number,
            expiry_date=expiry_date,
            quantity=quantity,
            unit_cost=medicine.unit_cost,
            landed_unit_cost=medicine.unit_cost,
            supplier=supplier,
            performed_by=actor,
            source_reference=source_reference,
        )
        return

    # No units yet: create the empty batch directly. receive_stock refuses a
    # zero quantity, and rightly so — a movement of nothing is not a movement.
    if expiry_date < timezone.localdate():
        raise BusinessRuleViolation(
            _("Batch %(batch)s has already expired and cannot be opened.")
            % {"batch": batch_number},
            code="expired_batch",
        )

    StockBatch.objects.create(
        product=medicine,
        warehouse=warehouse,
        batch_number=batch_number,
        expiry_date=expiry_date,
        supplier=supplier,
        quantity_received=Decimal("0"),
        quantity_remaining=Decimal("0"),
        quantity_reserved=Decimal("0"),
        unit_cost=medicine.unit_cost,
        landed_unit_cost=medicine.unit_cost,
        created_by=actor,
    )


@transaction.atomic
def create_medicine(*, actor=None, **data) -> Medicine:
    """Create a catalogue product, allocating a product code if absent."""
    if not data.get("product_code"):
        data["product_code"] = next_number("catalog.product")

    # Map frontend property fields to bilingual fr columns
    if "name" in data:
        data.setdefault("name_fr", data.pop("name"))
    if "brand_name" in data:
        data.setdefault("brand_name_fr", data.pop("brand_name"))
    if "notes" in data:
        data.setdefault("notes_fr", data.pop("notes"))
    if "strength" in data:
        data.setdefault("strength_fr", data.pop("strength"))

    # Opening-batch fields are captured on the product form but belong to
    # inventory, not the catalogue row — pop them before the model create.
    opening_quantity = data.pop("opening_quantity", None)
    opening_supplier = data.pop("opening_supplier", None)
    opening_warehouse = data.pop("opening_warehouse", None)
    batch_number = data.get("batch_number", "")
    expiry_date = data.get("expiry_date", None)

    medicine = Medicine.objects.create(created_by=actor, **data)

    if batch_number and expiry_date:
        _seed_opening_batch(
            medicine,
            batch_number=batch_number,
            expiry_date=expiry_date,
            quantity=opening_quantity,
            supplier_id=opening_supplier,
            warehouse_id=opening_warehouse,
            actor=actor,
            source_reference="PROD-CREATION",
        )

    # An opening price-history row means every product has a complete price
    # timeline from creation, with no gap before the first change.
    PriceHistory.objects.create(
        medicine=medicine,
        old_unit_cost=Decimal("0"),
        new_unit_cost=medicine.unit_cost,
        old_selling_price=Decimal("0"),
        new_selling_price=medicine.selling_price,
        reason=str(_("Initial pricing on product creation")),
        changed_by=actor,
    )

    record(
        AuditAction.CREATE,
        Medicine._meta.label,
        entity_id=str(medicine.pk),
        entity_label=medicine.display_name,
        new_value=snapshot(medicine),
        actor=actor,
    )
    return medicine


@transaction.atomic
def update_medicine(medicine: Medicine, *, actor=None, **data) -> Medicine:
    """
    Update a product, routing any price change through `change_price`.

    Price fields are intercepted rather than written directly: a price change
    that bypassed PriceHistory would leave an invoice unreconcilable against
    the catalogue, which is exactly what the history exists to prevent.
    """
    before = snapshot(medicine)

    new_cost = data.pop("unit_cost", None)
    new_price = data.pop("selling_price", None)
    price_reason = data.pop("price_change_reason", "")

    # Map frontend property fields to bilingual fr columns
    if "name" in data:
        data.setdefault("name_fr", data.pop("name"))
    if "brand_name" in data:
        data.setdefault("brand_name_fr", data.pop("brand_name"))
    if "notes" in data:
        data.setdefault("notes_fr", data.pop("notes"))
    if "strength" in data:
        data.setdefault("strength_fr", data.pop("strength"))

    opening_quantity = data.pop("opening_quantity", None)
    opening_supplier = data.pop("opening_supplier", None)
    opening_warehouse = data.pop("opening_warehouse", None)
    batch_number = data.get("batch_number")
    expiry_date = data.get("expiry_date")

    # Only a genuinely new lot opens a batch. Re-saving the form without
    # touching these fields must not re-book stock, or an innocent edit to a
    # product's notes would silently duplicate its opening quantity.
    #
    # A lot that already exists is *not* filtered out here. Whether it is a
    # harmless repeat or a conflicting expiry is a question about the batch,
    # and only `_seed_opening_batch` can see the answer: it returns quietly
    # when the expiry matches and raises when it does not. Deciding here on
    # the lot number alone would swallow the conflict.
    if batch_number and expiry_date and (
        batch_number != medicine.batch_number or expiry_date != medicine.expiry_date
    ):
        _seed_opening_batch(
            medicine,
            batch_number=batch_number,
            expiry_date=expiry_date,
            quantity=opening_quantity,
            supplier_id=opening_supplier,
            warehouse_id=opening_warehouse,
            actor=actor,
            source_reference="PROD-UPDATE",
        )

    for field, value in data.items():
        setattr(medicine, field, value)
    medicine.updated_by = actor
    medicine.save()

    price_changed = (new_cost is not None and new_cost != medicine.unit_cost) or (
        new_price is not None and new_price != medicine.selling_price
    )
    if price_changed:
        change_price(
            medicine,
            unit_cost=new_cost if new_cost is not None else medicine.unit_cost,
            selling_price=new_price if new_price is not None else medicine.selling_price,
            reason=price_reason,
            actor=actor,
        )

    record(
        AuditAction.UPDATE,
        Medicine._meta.label,
        entity_id=str(medicine.pk),
        entity_label=medicine.display_name,
        previous_value=before,
        new_value=snapshot(medicine),
        actor=actor,
    )
    return medicine


@transaction.atomic
def change_price(
    medicine: Medicine,
    *,
    unit_cost: Decimal | None = None,
    selling_price: Decimal | None = None,
    reason: str = "",
    actor=None,
) -> PriceHistory:
    """
    Change pricing, writing an immutable history row.

    A reason is mandatory. Price movement is a fraud-relevant event in
    wholesale distribution — an unexplained margin change is precisely what an
    audit looks for.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required for any price change."), code="reason_required"
        )

    old_cost = medicine.unit_cost
    old_price = medicine.selling_price
    new_cost = unit_cost if unit_cost is not None else old_cost
    new_price = selling_price if selling_price is not None else old_price

    if new_cost == old_cost and new_price == old_price:
        raise BusinessRuleViolation(
            _("The submitted prices are identical to the current ones."),
            code="no_price_change",
        )
    if new_price < 0 or new_cost < 0:
        raise BusinessRuleViolation(
            _("Prices cannot be negative."), code="invalid_price"
        )

    # Selling below cost is legitimate (clearance of short-dated stock) but
    # must be deliberate, so it is surfaced in the audit note rather than
    # blocked outright.
    below_cost = new_price < new_cost

    medicine.unit_cost = new_cost
    medicine.selling_price = new_price
    medicine.updated_by = actor
    medicine.save(update_fields=["unit_cost", "selling_price", "updated_by", "updated_at"])

    history = PriceHistory.objects.create(
        medicine=medicine,
        old_unit_cost=old_cost,
        new_unit_cost=new_cost,
        old_selling_price=old_price,
        new_selling_price=new_price,
        reason=reason,
        changed_by=actor,
    )

    record(
        AuditAction.PRICE_CHANGE,
        Medicine._meta.label,
        entity_id=str(medicine.pk),
        entity_label=medicine.display_name,
        previous_value={"unit_cost": str(old_cost), "selling_price": str(old_price)},
        new_value={"unit_cost": str(new_cost), "selling_price": str(new_price)},
        changed_fields=["unit_cost", "selling_price"],
        notes=(
            f"Price change: {reason}"
            + (" [WARNING: selling price is below cost]" if below_cost else "")
        ),
        actor=actor,
    )
    return history


@transaction.atomic
def set_status(medicine: Medicine, status: str, *, reason: str = "", actor=None) -> Medicine:
    """
    Change a product's lifecycle status.

    Discontinuing a product with stock on hand is blocked: the stock would
    become unsellable while still carrying value on the balance sheet. Sell
    through or write off first.
    """
    if status not in ProductStatus.values:
        raise BusinessRuleViolation(_("Unknown product status."), code="invalid_status")

    previous = medicine.status

    if status == ProductStatus.DISCONTINUED:
        from apps.inventory.models import BatchStatus, StockBatch

        on_hand = StockBatch.objects.filter(
            product=medicine,
            status__in=[BatchStatus.ACTIVE, BatchStatus.QUARANTINED],
            quantity_remaining__gt=0,
            deleted_at__isnull=True,
        ).exists()
        if on_hand:
            raise BusinessRuleViolation(
                _("This product cannot be discontinued while stock remains on hand."),
                code="stock_on_hand",
            )

    medicine.status = status
    medicine.updated_by = actor
    medicine.save(update_fields=["status", "updated_by", "updated_at"])

    record(
        AuditAction.UPDATE,
        Medicine._meta.label,
        entity_id=str(medicine.pk),
        entity_label=medicine.display_name,
        previous_value={"status": previous},
        new_value={"status": status},
        changed_fields=["status"],
        notes=f"Status changed: {reason}" if reason else "Status changed",
        actor=actor,
    )
    return medicine


def price_on_date(medicine: Medicine, when):
    """
    The selling price in force at a given moment.

    Used when reprinting or reconciling a historical invoice — showing today's
    price against a two-year-old document would be wrong.
    """
    entry = (
        medicine.price_history.filter(effective_from__lte=when)
        .order_by("-effective_from")
        .first()
    )
    return entry.new_selling_price if entry else medicine.selling_price
