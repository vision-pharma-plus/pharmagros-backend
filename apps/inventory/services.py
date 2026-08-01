"""
Stock services: FIFO allocation, receipt, issue, adjustment, transfer.

Every quantity change in the system funnels through `_post_movement`. That is
the single invariant this module protects: batch balances and the ledger can
never disagree, because they are written together in one transaction.

Concurrency model
-----------------
Allocation locks candidate batch rows with SELECT ... FOR UPDATE before
reading their balances. Without the lock, two concurrent sales can both read
"120 available", both allocate 100, and drive the batch negative — the
classic lost-update race. The database CHECK constraint would then reject the
second write, but only after the caller believed it had succeeded.

Locking rows in a deterministic order (expiry, then received date, then id)
also prevents deadlock between two allocations touching overlapping batches.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record
from apps.core.exceptions import (
    BusinessRuleViolation,
    ExpiredBatchError,
    InsufficientStock,
)
from apps.core.models import AuditAction
from apps.core.money import q_internal

from .models import (
    OUTBOUND_TYPES,
    BatchStatus,
    MovementType,
    StockBatch,
    StockMovement,
    StockReservation,
    Warehouse,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


@dataclass
class Allocation:
    """One batch's contribution to fulfilling a requested quantity."""

    batch: StockBatch
    quantity: Decimal
    unit_cost: Decimal

    @property
    def value(self) -> Decimal:
        return q_internal(self.quantity * self.unit_cost)


# ---------------------------------------------------------------------------
# FIFO allocation
# ---------------------------------------------------------------------------


def allocate_fifo(
    product,
    quantity: Decimal,
    *,
    warehouse: Warehouse,
    lock: bool = True,
    allow_partial: bool = False,
    exclude_batches: Iterable[str] = (),
) -> list[Allocation]:
    """
    Choose batches to satisfy `quantity`, oldest-expiry-first.

    FEFO, strictly speaking: ordering is by expiry date, not receipt date.
    In pharmaceutical distribution these usually coincide, but when they do
    not, shipping the earliest-expiring stock first is what prevents writing
    off inventory that was sitting behind newer, longer-dated boxes. Receipt
    date is the tie-breaker so identical expiries still behave as FIFO.

    Expired and non-ACTIVE batches are excluded at the query level — an
    expired batch must be incapable of being sold, not merely discouraged.

    Callers must be inside a transaction when lock=True.
    """
    quantity = q_internal(quantity)
    if quantity <= ZERO:
        raise BusinessRuleViolation(
            _("The quantity to allocate must be greater than zero."),
            code="invalid_quantity",
        )

    today = timezone.localdate()

    candidates = (
        StockBatch.objects.filter(
            product=product,
            warehouse=warehouse,
            status=BatchStatus.ACTIVE,
            expiry_date__gte=today,
            deleted_at__isnull=True,
        )
        .exclude(id__in=list(exclude_batches))
        # Deterministic order: prevents deadlocks between concurrent
        # allocations and makes the pick reproducible for auditing.
        .order_by("expiry_date", "received_at", "id")
    )

    if lock:
        candidates = candidates.select_for_update()

    allocations: list[Allocation] = []
    outstanding = quantity

    for batch in candidates:
        if outstanding <= ZERO:
            break
        available = batch.quantity_remaining - batch.quantity_reserved
        if available <= ZERO:
            continue

        take = min(available, outstanding)
        allocations.append(
            Allocation(batch=batch, quantity=take, unit_cost=batch.landed_unit_cost)
        )
        outstanding = q_internal(outstanding - take)

    if outstanding > ZERO and not allow_partial:
        on_hand = (
            StockBatch.objects.filter(
                product=product, warehouse=warehouse,
                status=BatchStatus.ACTIVE, expiry_date__gte=today,
                deleted_at__isnull=True,
            ).aggregate(
                total=Sum(F("quantity_remaining") - F("quantity_reserved"))
            )["total"]
            or ZERO
        )
        raise InsufficientStock(
            _("Insufficient stock for %(product)s: %(requested)s requested, %(available)s available.")
            % {
                "product": getattr(product, "name", str(product)),
                "requested": quantity,
                "available": on_hand,
            },
            details={
                "product_id": str(product.pk),
                "requested": str(quantity),
                "available": str(on_hand),
                "shortfall": str(outstanding),
            },
        )

    return allocations


# ---------------------------------------------------------------------------
# Movement posting — the single write path
# ---------------------------------------------------------------------------


def _post_movement(
    batch: StockBatch,
    movement_type: str,
    quantity: Decimal,
    *,
    unit_cost: Decimal | None = None,
    source_type: str = "",
    source_id: str = "",
    source_reference: str = "",
    performed_by=None,
    reason: str = "",
    notes: str = "",
) -> StockMovement:
    """
    Apply a signed quantity change to a batch and journal it.

    `quantity` is always supplied positive; direction comes from
    movement_type. Making callers reason about signs is a reliable source of
    inverted-stock bugs.

    Must run inside a transaction with the batch row already locked.
    """
    quantity = q_internal(abs(quantity))
    if quantity <= ZERO:
        raise BusinessRuleViolation(
            _("A stock movement must have a quantity greater than zero."),
            code="invalid_quantity",
        )

    outbound = movement_type in OUTBOUND_TYPES
    delta = -quantity if outbound else quantity
    new_balance = q_internal(batch.quantity_remaining + delta)

    if new_balance < ZERO:
        raise InsufficientStock(
            _("Batch %(batch)s holds %(available)s units; %(requested)s were requested.")
            % {
                "batch": batch.batch_number,
                "available": batch.quantity_remaining,
                "requested": quantity,
            },
            details={
                "batch_id": str(batch.pk),
                "available": str(batch.quantity_remaining),
                "requested": str(quantity),
            },
        )

    effective_cost = unit_cost if unit_cost is not None else batch.landed_unit_cost

    movement = StockMovement.objects.create(
        batch=batch,
        product_id=batch.product_id,
        warehouse_id=batch.warehouse_id,
        movement_type=movement_type,
        quantity_delta=delta,
        balance_after=new_balance,
        unit_cost=effective_cost,
        total_value=q_internal(abs(delta) * effective_cost),
        source_type=source_type,
        source_id=str(source_id),
        source_reference=source_reference,
        performed_by=performed_by,
        reason=reason,
        notes=notes,
    )

    batch.quantity_remaining = new_balance
    if new_balance == ZERO and batch.status == BatchStatus.ACTIVE:
        batch.status = BatchStatus.DEPLETED
    elif new_balance > ZERO and batch.status == BatchStatus.DEPLETED:
        # A return into a depleted batch reactivates it.
        batch.status = BatchStatus.ACTIVE
    batch.save(update_fields=["quantity_remaining", "status", "updated_at"])

    return movement


@transaction.atomic
def post_batch_movement(
    batch: StockBatch,
    movement_type: str,
    quantity: Decimal,
    *,
    unit_cost: Decimal | None = None,
    source_type: str = "",
    source_id: str = "",
    source_reference: str = "",
    performed_by=None,
    reason: str = "",
    notes: str = "",
) -> StockMovement:
    """
    Public entry point for posting a movement against an already-locked batch.

    Other apps (sales returns, purchase receipts) need to write to the ledger
    for a batch they have themselves selected, rather than going through FIFO
    allocation. They call this rather than the private `_post_movement`, so
    the ledger keeps a single documented write path across app boundaries.

    Callers must hold a `select_for_update()` lock on `batch`.
    """
    return _post_movement(
        batch,
        movement_type,
        quantity,
        unit_cost=unit_cost,
        source_type=source_type,
        source_id=source_id,
        source_reference=source_reference,
        performed_by=performed_by,
        reason=reason,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------


@transaction.atomic
def receive_stock(
    *,
    product,
    warehouse: Warehouse,
    batch_number: str,
    expiry_date,
    quantity: Decimal,
    unit_cost: Decimal,
    supplier=None,
    manufacturing_date=None,
    landed_unit_cost: Decimal | None = None,
    purchase_order=None,
    source_reference: str = "",
    performed_by=None,
    notes: str = "",
    movement_type: str = MovementType.RECEIPT,
) -> tuple[StockBatch, StockMovement]:
    """
    Receive goods into stock, creating or topping up a batch.

    Refuses already-expired goods: accepting them would put unsellable stock
    into the valuation and, worse, make it pickable if the expiry check ever
    regressed. Rejecting at the door is the safer failure.

    `movement_type` exists for opening balances, which enter stock by the same
    mechanism but are not a purchase: they are asserted from a physical count
    at go-live. Journalling both as RECEIPT would overstate supplier purchase
    history by the entire starting inventory.
    """
    if movement_type not in {MovementType.RECEIPT, MovementType.OPENING}:
        raise BusinessRuleViolation(
            _("Stock can only be received as a goods receipt or an opening balance."),
            code="invalid_movement_type",
        )
    quantity = q_internal(quantity)
    if quantity <= ZERO:
        raise BusinessRuleViolation(
            _("The quantity received must be greater than zero."), code="invalid_quantity"
        )

    if expiry_date < timezone.localdate():
        raise ExpiredBatchError(
            _("Batch %(batch)s expired on %(date)s and cannot be received into stock.")
            % {"batch": batch_number, "date": expiry_date},
            details={"batch_number": batch_number, "expiry_date": str(expiry_date)},
        )

    landed = landed_unit_cost if landed_unit_cost is not None else unit_cost

    batch = (
        StockBatch.objects.select_for_update()
        .filter(
            product=product, warehouse=warehouse,
            batch_number=batch_number, deleted_at__isnull=True,
        )
        .first()
    )

    if batch is None:
        batch = StockBatch.objects.create(
            product=product,
            warehouse=warehouse,
            batch_number=batch_number,
            manufacturing_date=manufacturing_date,
            expiry_date=expiry_date,
            supplier=supplier,
            purchase_order=purchase_order,
            quantity_received=quantity,
            quantity_remaining=ZERO,  # set by the movement below
            unit_cost=unit_cost,
            landed_unit_cost=landed,
            created_by=performed_by,
            notes=notes,
        )
    else:
        if batch.expiry_date != expiry_date:
            # Same batch number with a different expiry means either a data
            # entry error or a genuinely different lot reusing a number.
            # Both need a human decision, not a silent merge.
            raise BusinessRuleViolation(
                _("Batch %(batch)s already exists with expiry %(existing)s, which differs from the %(new)s supplied.")
                % {"batch": batch_number, "existing": batch.expiry_date, "new": expiry_date},
                code="batch_expiry_conflict",
            )
        # Weighted-average the cost across the combined quantity. Keeping the
        # original cost would misvalue the topped-up units; overwriting it
        # would misvalue the units already held.
        existing_value = batch.quantity_remaining * batch.landed_unit_cost
        incoming_value = quantity * landed
        combined_qty = batch.quantity_remaining + quantity
        if combined_qty > ZERO:
            batch.landed_unit_cost = q_internal((existing_value + incoming_value) / combined_qty)
        batch.quantity_received = q_internal(batch.quantity_received + quantity)
        batch.unit_cost = unit_cost
        if batch.status in {BatchStatus.DEPLETED}:
            batch.status = BatchStatus.ACTIVE
        batch.save(
            update_fields=[
                "quantity_received", "unit_cost", "landed_unit_cost", "status", "updated_at",
            ]
        )

    is_opening = movement_type == MovementType.OPENING
    if is_opening:
        source_type = "inventory.OpeningBalance"
    elif purchase_order:
        source_type = "purchasing.GoodsReceipt"
    else:
        source_type = "inventory.Receipt"

    movement = _post_movement(
        batch,
        movement_type,
        quantity,
        unit_cost=landed,
        source_type=source_type,
        source_id=str(getattr(purchase_order, "pk", "")),
        source_reference=source_reference,
        performed_by=performed_by,
        notes=notes,
    )

    record(
        AuditAction.STOCK_MOVEMENT,
        StockBatch._meta.label,
        entity_id=str(batch.pk),
        entity_label=str(batch),
        new_value={
            "movement": movement_type,
            "quantity": str(quantity),
            "unit_cost": str(landed),
            "balance_after": str(batch.quantity_remaining),
        },
        notes=(
            f"Opening balance {source_reference}".strip()
            if is_opening
            else f"Goods receipt {source_reference}".strip()
        ),
        actor=performed_by,
    )
    return batch, movement


@transaction.atomic
def issue_stock(
    *,
    product,
    warehouse: Warehouse,
    quantity: Decimal,
    source_type: str,
    source_id: str,
    source_reference: str = "",
    performed_by=None,
    movement_type: str = MovementType.ISSUE,
    reason: str = "",
) -> list[StockMovement]:
    """
    Issue stock using FIFO/FEFO, spanning as many batches as required.

    Returns one movement per batch touched, so the caller (a sales invoice
    line, typically) can record exactly which batches shipped — the
    information a recall depends on.
    """
    allocations = allocate_fifo(product, quantity, warehouse=warehouse, lock=True)

    movements = []
    for allocation in allocations:
        movements.append(
            _post_movement(
                allocation.batch,
                movement_type,
                allocation.quantity,
                unit_cost=allocation.unit_cost,
                source_type=source_type,
                source_id=source_id,
                source_reference=source_reference,
                performed_by=performed_by,
                reason=reason,
            )
        )

    record(
        AuditAction.STOCK_MOVEMENT,
        "inventory.StockIssue",
        entity_id=str(source_id),
        entity_label=source_reference,
        new_value={
            "product": str(product.pk),
            "quantity": str(quantity),
            "batches": [
                {"batch": a.batch.batch_number, "quantity": str(a.quantity)} for a in allocations
            ],
        },
        notes=f"FIFO issue for {source_type} {source_reference}",
        actor=performed_by,
    )
    return movements


@transaction.atomic
def return_stock_to_batch(
    *,
    batch_id,
    quantity: Decimal,
    source_type: str,
    source_id: str,
    source_reference: str = "",
    performed_by=None,
    reason: str = "",
) -> StockMovement:
    """
    Return goods into the batch they came from.

    Returning into the *original* batch rather than a generic pool preserves
    the expiry date and cost of the returned units. Putting them into an
    arbitrary batch would corrupt both expiry tracking and valuation.

    Note: a returned pharmaceutical is only saleable again if storage
    conditions were maintained. The sales-return service quarantines returns
    that fail that check before calling this.
    """
    batch = StockBatch.objects.select_for_update().get(pk=batch_id)

    if batch.is_expired:
        raise ExpiredBatchError(
            _("Batch %(batch)s has expired; returned units must be quarantined for disposal.")
            % {"batch": batch.batch_number}
        )

    movement = _post_movement(
        batch,
        MovementType.SALE_RETURN,
        quantity,
        source_type=source_type,
        source_id=source_id,
        source_reference=source_reference,
        performed_by=performed_by,
        reason=reason,
    )

    record(
        AuditAction.STOCK_MOVEMENT,
        StockBatch._meta.label,
        entity_id=str(batch.pk),
        entity_label=str(batch),
        new_value={
            "movement": MovementType.SALE_RETURN,
            "quantity": str(quantity),
            "balance_after": str(batch.quantity_remaining),
        },
        notes=f"Customer return {source_reference}: {reason}",
        actor=performed_by,
    )
    return movement


@transaction.atomic
def adjust_stock(
    *,
    batch_id,
    new_quantity: Decimal,
    reason: str,
    performed_by=None,
    source_reference: str = "",
    notes: str = "",
) -> StockMovement | None:
    """
    Correct a batch balance to a counted figure (stocktake).

    A reason is mandatory. An unexplained inventory adjustment is precisely
    what an auditor looks for when investigating diversion, so the system
    refuses to record one.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required for every stock adjustment."), code="reason_required"
        )

    batch = StockBatch.objects.select_for_update().get(pk=batch_id)
    new_quantity = q_internal(new_quantity)

    if new_quantity < ZERO:
        raise BusinessRuleViolation(
            _("The adjusted quantity cannot be negative."), code="invalid_quantity"
        )

    delta = q_internal(new_quantity - batch.quantity_remaining)
    if delta == ZERO:
        return None

    movement_type = MovementType.ADJUSTMENT_IN if delta > ZERO else MovementType.ADJUSTMENT_OUT
    previous = batch.quantity_remaining

    movement = _post_movement(
        batch,
        movement_type,
        abs(delta),
        source_type="inventory.StockAdjustment",
        source_id=str(batch.pk),
        source_reference=source_reference,
        performed_by=performed_by,
        reason=reason,
        notes=notes,
    )

    record(
        AuditAction.STOCK_MOVEMENT,
        StockBatch._meta.label,
        entity_id=str(batch.pk),
        entity_label=str(batch),
        previous_value={"quantity_remaining": str(previous)},
        new_value={"quantity_remaining": str(new_quantity), "delta": str(delta)},
        changed_fields=["quantity_remaining"],
        notes=f"Stock adjustment: {reason}",
        actor=performed_by,
    )
    return movement


@transaction.atomic
def transfer_stock(
    *,
    batch_id,
    destination: Warehouse,
    quantity: Decimal,
    performed_by=None,
    source_reference: str = "",
    reason: str = "",
) -> tuple[StockMovement, StockMovement]:
    """
    Move stock between warehouses.

    Implemented as a paired out/in movement against a mirror batch at the
    destination, preserving batch number, expiry and cost. Transferring
    without preserving batch identity would break traceability the moment
    stock crosses a location boundary.
    """
    source_batch = StockBatch.objects.select_for_update().get(pk=batch_id)

    if source_batch.warehouse_id == destination.pk:
        raise BusinessRuleViolation(
            _("The source and destination warehouses must differ."), code="same_warehouse"
        )
    if source_batch.is_expired:
        raise ExpiredBatchError(
            _("Expired batch %(batch)s cannot be transferred; it must be disposed of.")
            % {"batch": source_batch.batch_number}
        )

    out_movement = _post_movement(
        source_batch,
        MovementType.TRANSFER_OUT,
        quantity,
        source_type="inventory.StockTransfer",
        source_id=str(source_batch.pk),
        source_reference=source_reference,
        performed_by=performed_by,
        reason=reason,
    )

    destination_batch = (
        StockBatch.objects.select_for_update()
        .filter(
            product_id=source_batch.product_id,
            warehouse=destination,
            batch_number=source_batch.batch_number,
            deleted_at__isnull=True,
        )
        .first()
    )
    if destination_batch is None:
        destination_batch = StockBatch.objects.create(
            product_id=source_batch.product_id,
            warehouse=destination,
            batch_number=source_batch.batch_number,
            manufacturing_date=source_batch.manufacturing_date,
            expiry_date=source_batch.expiry_date,
            supplier_id=source_batch.supplier_id,
            quantity_received=ZERO,
            quantity_remaining=ZERO,
            unit_cost=source_batch.unit_cost,
            landed_unit_cost=source_batch.landed_unit_cost,
            created_by=performed_by,
        )

    in_movement = _post_movement(
        destination_batch,
        MovementType.TRANSFER_IN,
        quantity,
        unit_cost=source_batch.landed_unit_cost,
        source_type="inventory.StockTransfer",
        source_id=str(source_batch.pk),
        source_reference=source_reference,
        performed_by=performed_by,
        reason=reason,
    )

    record(
        AuditAction.STOCK_MOVEMENT,
        "inventory.StockTransfer",
        entity_id=str(source_batch.pk),
        entity_label=f"{source_batch.batch_number} -> {destination.code}",
        new_value={
            "quantity": str(quantity),
            "from": source_batch.warehouse.code,
            "to": destination.code,
        },
        notes=f"Stock transfer: {reason}",
        actor=performed_by,
    )
    return out_movement, in_movement


@transaction.atomic
def write_off_stock(
    *,
    batch_id,
    quantity: Decimal,
    movement_type: str,
    reason: str,
    performed_by=None,
    source_reference: str = "",
) -> StockMovement:
    """
    Remove stock for damage, expiry or disposal.

    Disposal of expired pharmaceuticals is a regulated act — the record here
    is the evidence that stock was destroyed rather than diverted, so the
    reason and the acting user are both mandatory.
    """
    if movement_type not in {MovementType.DAMAGE, MovementType.EXPIRY, MovementType.DISPOSAL}:
        raise BusinessRuleViolation(
            _("Invalid write-off type."), code="invalid_movement_type"
        )
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required for a stock write-off."), code="reason_required"
        )

    batch = StockBatch.objects.select_for_update().get(pk=batch_id)

    movement = _post_movement(
        batch,
        movement_type,
        quantity,
        source_type="inventory.StockDisposal",
        source_id=str(batch.pk),
        source_reference=source_reference,
        performed_by=performed_by,
        reason=reason,
    )

    if batch.quantity_remaining == ZERO:
        batch.status = (
            BatchStatus.DISPOSED
            if movement_type == MovementType.DISPOSAL
            else BatchStatus.DAMAGED
            if movement_type == MovementType.DAMAGE
            else BatchStatus.EXPIRED
        )
        batch.save(update_fields=["status", "updated_at"])

    record(
        AuditAction.STOCK_MOVEMENT,
        StockBatch._meta.label,
        entity_id=str(batch.pk),
        entity_label=str(batch),
        new_value={
            "movement": movement_type,
            "quantity": str(quantity),
            "balance_after": str(batch.quantity_remaining),
        },
        notes=f"Write-off ({movement_type}): {reason}",
        actor=performed_by,
    )
    return movement


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------


@transaction.atomic
def reserve_stock(
    *, product, warehouse: Warehouse, quantity: Decimal,
    source_type: str, source_id: str, ttl_minutes: int = 30,
) -> list[StockReservation]:
    """Hold stock for a draft document so concurrent sales cannot oversell."""
    allocations = allocate_fifo(product, quantity, warehouse=warehouse, lock=True)
    expires_at = timezone.now() + timezone.timedelta(minutes=ttl_minutes)

    reservations = []
    for allocation in allocations:
        batch = allocation.batch
        batch.quantity_reserved = q_internal(batch.quantity_reserved + allocation.quantity)
        batch.save(update_fields=["quantity_reserved", "updated_at"])
        reservations.append(
            StockReservation.objects.create(
                batch=batch, quantity=allocation.quantity,
                source_type=source_type, source_id=str(source_id), expires_at=expires_at,
            )
        )
    return reservations


@transaction.atomic
def release_reservations(*, source_type: str, source_id: str) -> int:
    """Release all active reservations held by a document."""
    reservations = StockReservation.objects.select_for_update().filter(
        source_type=source_type, source_id=str(source_id), released_at__isnull=True,
    )
    count = 0
    for reservation in reservations:
        batch = StockBatch.objects.select_for_update().get(pk=reservation.batch_id)
        batch.quantity_reserved = q_internal(
            max(ZERO, batch.quantity_reserved - reservation.quantity)
        )
        batch.save(update_fields=["quantity_reserved", "updated_at"])
        reservation.released_at = timezone.now()
        reservation.save(update_fields=["released_at"])
        count += 1
    return count


# ---------------------------------------------------------------------------
# Valuation & reconciliation
# ---------------------------------------------------------------------------


def inventory_valuation(*, warehouse: Warehouse | None = None, as_of=None) -> dict:
    """
    Total stock value at landed cost.

    Valued at landed cost, not selling price: inventory is an asset carried
    at what it cost to acquire, and using selling price would overstate the
    balance sheet by the full margin.
    """
    batches = StockBatch.objects.filter(
        deleted_at__isnull=True,
        status__in=[BatchStatus.ACTIVE, BatchStatus.QUARANTINED],
        quantity_remaining__gt=ZERO,
    )
    if warehouse:
        batches = batches.filter(warehouse=warehouse)

    total_value = ZERO
    total_units = ZERO
    product_ids = set()
    for batch in batches.iterator(chunk_size=500):
        total_value += batch.quantity_remaining * batch.landed_unit_cost
        total_units += batch.quantity_remaining
        product_ids.add(batch.product_id)

    return {
        "total_value": q_internal(total_value),
        "total_units": q_internal(total_units),
        "distinct_products": len(product_ids),
        "batch_count": batches.count(),
        "as_of": as_of or timezone.now(),
        "currency": "BIF",
    }


def reconcile_batch(batch: StockBatch) -> dict:
    """Compare the cached balance against the ledger sum."""
    ledger = batch.ledger_balance()
    cached = batch.quantity_remaining
    return {
        "batch_id": str(batch.pk),
        "batch_number": batch.batch_number,
        "cached_balance": cached,
        "ledger_balance": ledger,
        "discrepancy": q_internal(cached - ledger),
        "reconciled": cached == ledger,
    }


def find_discrepancies(*, warehouse: Warehouse | None = None) -> list[dict]:
    """
    Every batch whose cache disagrees with its ledger.

    Should always return empty. A non-empty result means either a bug in the
    write path or out-of-band database modification — both are incidents.
    """
    batches = StockBatch.objects.filter(deleted_at__isnull=True)
    if warehouse:
        batches = batches.filter(warehouse=warehouse)

    discrepancies = []
    for batch in batches.iterator(chunk_size=500):
        result = reconcile_batch(batch)
        if not result["reconciled"]:
            discrepancies.append(result)
    return discrepancies
